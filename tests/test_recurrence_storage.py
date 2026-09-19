"""Recurring-task acceptance, isolation, recovery and native concurrency tests."""

import copy
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import patch
from uuid import UUID, uuid4

DB_URL = os.getenv("SMARTPLANNER_TEST_DATABASE_URL")
FLAVOR = os.getenv("SMARTPLANNER_TEST_DB_FLAVOR", "native")
ROOT = "/api/v1/recurring-activities"
ORIGIN = "http://localhost:8000"
PASSWORD = "Recurrence tests only 2026!"

if DB_URL:
    import psycopg
    from fastapi.testclient import TestClient
    from smartplanner.api import create_app
    from smartplanner.database import connect
    from smartplanner.errors import AppError
    from smartplanner.migrate import migrate
    from smartplanner.recurrence_models import OccurrenceRequest, RecurrenceCreate, RecurrenceReplace
    from smartplanner.recurrence_repository import RecurrenceRepository
    from smartplanner.settings import Settings


@unittest.skipUnless(DB_URL, "Select a disposable PostgreSQL database.")
class RecurrenceStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate(DB_URL, Path("db/migrations"))
        cls.app = create_app(Settings(DB_URL, auth_requests_per_minute=1000))
        cls.a, cls.b = TestClient(cls.app, base_url=ORIGIN), TestClient(cls.app, base_url=ORIGIN)
        for client in (cls.a, cls.b):
            email = f"recurrence-{uuid4().hex}@example.com"
            response = client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
            assert response.status_code == 201, response.text
            response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
            assert response.status_code == 200, response.text
            client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        cls.owner = UUID(cls.a.get("/api/v1/me").json()["id"])
        cls.other_owner = UUID(cls.b.get("/api/v1/me").json()["id"])
        cls.counter = 0

    @classmethod
    def tearDownClass(cls):
        cls.a.close()
        cls.b.close()

    def setUp(self):
        type(self).counter += 1
        self.day = datetime.now(timezone.utc).date() + timedelta(days=14 * self.counter)

    def create(self, **changes):
        data = {"client_request_id": str(uuid4()), "title": "انگلیسی", "duration_minutes": 60,
                "frequency": "daily", "start_date": self.day.isoformat(), **changes}
        response = self.a.post(ROOT, json=data)
        self.assertEqual(response.status_code, 201, response.text)
        return data, response.json()

    def values(self, record, **changes):
        return {key: value for key, value in {**record, **changes}.items()
                if key not in {"id", "user_id", "client_request_id", "timezone", "version", "created_at", "updated_at"}}

    def generate(self, series, **changes):
        response = self.a.post(f"{ROOT}/{series['id']}/occurrences", json={
            "expected_version": series["version"], "start_date": self.day.isoformat(), "days": 7, **changes})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def replace(self, series, **changes):
        response = self.a.put(f"{ROOT}/{series['id']}", json={"expected_version": series["version"], "values": self.values(series, **changes)})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def task_count(self, series):
        with connect(DB_URL) as conn:
            return conn.execute("SELECT count(*) AS n FROM tasks WHERE recurrence_id=%s", (UUID(series["id"]),)).fetchone()["n"]

    def test_01_create_get_list_and_retry_after_edit(self):
        data, series = self.create()
        self.assertEqual(self.a.get(f"{ROOT}/{series['id']}").json(), series)
        self.assertIn(series, self.a.get(ROOT).json())
        changed = self.replace(series, title="واژگان")
        replay = self.a.post(ROOT, json=data)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json(), changed)
        conflict = self.a.post(ROOT, json={**data, "duration_minutes": 30})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(migrate(DB_URL, Path("db/migrations")), [])

    def test_02_every_route_is_owner_scoped(self):
        _, series = self.create()
        path = f"{ROOT}/{series['id']}"
        self.assertEqual(self.b.get(path).status_code, 404)
        self.assertEqual(self.b.put(path, json={"expected_version": 1, "values": self.values(series)}).status_code, 404)
        self.assertEqual(self.b.delete(path, params={"expected_version": 1}).status_code, 404)
        self.assertEqual(self.b.post(path + "/occurrences", json={"expected_version": 1, "start_date": self.day.isoformat()}).status_code, 404)
        self.assertNotIn(series["id"], [row["id"] for row in self.b.get(ROOT).json()])
        tasks = self.generate(series)["tasks"]
        self.assertEqual(self.b.get(f"/api/v1/tasks/{tasks[0]['id']}").status_code, 404)

    def test_03_auth_csrf_and_provenance_injection(self):
        data, series = self.create()
        with TestClient(self.app, base_url=ORIGIN) as client:
            self.assertEqual(client.get(ROOT).status_code, 401)
        path = f"{ROOT}/{series['id']}"
        self.assertEqual(self.a.post(path + "/occurrences", json={"expected_version": 1, "start_date": self.day.isoformat()}, headers={"X-CSRF-Token": "bad"}).status_code, 403)
        for key, value in (("timezone", "Asia/Tehran"), ("user_id", str(self.other_owner)), ("version", 2)):
            self.assertEqual(self.a.post(ROOT, json={**data, key: value}).status_code, 422)
        task = self.generate(series, days=1)["tasks"][0]
        for field in ("recurrence_id", "occurrence_date"):
            self.assertEqual(self.a.patch(f"/api/v1/tasks/{task['id']}", json={"expected_version": 1, field: None}).status_code, 422)
        forged = {"client_request_id": str(uuid4()), "title": "forged", "duration_minutes": 5, "recurrence_id": series["id"]}
        self.assertEqual(self.a.post("/api/v1/tasks", json=forged).status_code, 422)

    def test_04_repeated_and_overlapping_generation_keeps_one_task_per_day(self):
        _, series = self.create()
        first = self.generate(series)
        self.assertEqual(first["created_count"], 7)
        self.assertEqual(len({task["id"] for task in first["tasks"]}), 7)
        replay = self.generate(series)
        self.assertEqual(replay["created_count"], 0)
        self.assertEqual(replay["tasks"], first["tasks"])
        overlap = self.generate(series, start_date=(self.day + timedelta(days=5)).isoformat())
        self.assertEqual(overlap["created_count"], 5)
        self.assertEqual([task["id"] for task in overlap["tasks"][:2]], [task["id"] for task in first["tasks"][-2:]])
        self.assertEqual(self.task_count(series), 12)
        self.assertTrue(all(task["recurrence_id"] == series["id"] for task in first["tasks"]))

    def test_05_complete_cancel_delete_and_move_are_independent(self):
        _, series = self.create()
        first = self.generate(series)["tasks"]
        for task, status in zip(first, ("completed", "cancelled")):
            self.assertEqual(self.a.patch(f"/api/v1/tasks/{task['id']}", json={"expected_version": 1, "status": status}).status_code, 200)
        self.assertEqual(self.a.delete(f"/api/v1/tasks/{first[2]['id']}", params={"expected_version": 1}).status_code, 204)
        moved = self.a.patch(f"/api/v1/tasks/{first[3]['id']}", json={"expected_version": 1, "earliest_start": None, "deadline": None})
        self.assertEqual(moved.status_code, 200, moved.text)
        replay = self.generate(series)
        self.assertEqual(replay["created_count"], 0)
        self.assertEqual(replay["deleted_dates"], [first[2]["occurrence_date"]])
        self.assertEqual([task["status"] for task in replay["tasks"][:2]], ["completed", "cancelled"])
        self.assertEqual(replay["tasks"][2], moved.json())
        self.assertEqual(replay["tasks"][-1], first[-1])
        self.assertEqual(self.task_count(series), 7)

    def test_06_pattern_edit_preserves_old_instances_including_removed_weekdays(self):
        _, series = self.create()
        first = self.generate(series, days=1)["tasks"][0]
        changed = self.replace(series, frequency="weekly", start_date=(self.day + timedelta(days=1)).isoformat(), duration_minutes=30, title="کوتاه‌تر")
        result = self.generate(changed)
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(result["tasks"][0], first)
        self.assertEqual(result["tasks"][1]["duration_minutes"], 30)
        self.assertEqual(result["tasks"][1]["title"], "کوتاه‌تر")

    def test_07_pause_resume_and_delete_keep_existing_tasks(self):
        data, series = self.create()
        task = self.generate(series, days=1)["tasks"][0]
        paused = self.replace(series, active=False)
        result = self.generate(paused)
        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["tasks"], [task])
        resumed = self.replace(paused, active=True)
        self.assertEqual(self.generate(resumed)["created_count"], 6)
        path = f"{ROOT}/{series['id']}"
        self.assertEqual(self.a.delete(path, params={"expected_version": resumed["version"]}).status_code, 204)
        self.assertEqual(self.a.get(path).status_code, 404)
        self.assertEqual(self.a.post(path + "/occurrences", json={"expected_version": resumed["version"], "start_date": self.day.isoformat()}).status_code, 404)
        self.assertEqual(self.a.post(ROOT, json=data).status_code, 409)
        self.assertEqual(self.a.get(f"/api/v1/tasks/{task['id']}").json(), task)

    def test_08_stale_edit_delete_and_generation_do_not_change_data(self):
        _, series = self.create()
        changed = self.replace(series, duration_minutes=30)
        path = f"{ROOT}/{series['id']}"
        self.assertEqual(self.a.put(path, json={"expected_version": 1, "values": self.values(series)}).status_code, 409)
        self.assertEqual(self.a.delete(path, params={"expected_version": 1}).status_code, 409)
        self.assertEqual(self.a.post(path + "/occurrences", json={"expected_version": 1, "start_date": self.day.isoformat()}).status_code, 409)
        self.assertEqual(self.a.get(path).json(), changed)
        self.assertEqual(self.task_count(series), 0)

    def test_09_local_today_skips_uncreated_past_without_hiding_existing(self):
        _, series = self.create(start_date="2026-09-19")
        request = OccurrenceRequest(expected_version=1, start_date="2026-09-19", days=7)
        with connect(DB_URL) as conn:
            repo = RecurrenceRepository(conn)
            first = repo.materialize(self.owner, UUID(series["id"]), request, now=datetime(2026, 9, 22, tzinfo=timezone.utc))
            self.assertEqual(first.created_count, 4)
            self.assertEqual(first.skipped_past_dates, (date(2026, 9, 19), date(2026, 9, 20), date(2026, 9, 21)))
            later = repo.materialize(self.owner, UUID(series["id"]), request, now=datetime(2026, 9, 27, tzinfo=timezone.utc))
            self.assertEqual(later.tasks, first.tasks)
            self.assertEqual(later.created_count, 0)

    def test_10_invalid_dst_day_rolls_back_earlier_days(self):
        _, series = self.create(start_date="2026-03-06", duration_minutes=1440)
        with connect(DB_URL) as conn:
            conn.execute("UPDATE recurring_activities SET timezone='America/New_York' WHERE id=%s", (UUID(series["id"]),))
            with self.assertRaises(AppError) as caught:
                RecurrenceRepository(conn).materialize(self.owner, UUID(series["id"]), OccurrenceRequest(expected_version=1, start_date="2026-03-06"), now=datetime(2026, 3, 1, tzinfo=timezone.utc))
            self.assertEqual(caught.exception.code, "occurrence_too_long")
        self.assertEqual(self.task_count(series), 0)

    def test_11_failure_midway_is_atomic_and_retry_succeeds(self):
        _, series = self.create()
        original = RecurrenceRepository._insert_occurrences
        def fail_after_insert(repo, *args):
            original(repo, *args)
            raise AppError(503, "simulated_failure", "retry")
        body = {"expected_version": 1, "start_date": self.day.isoformat()}
        with patch.object(RecurrenceRepository, "_insert_occurrences", fail_after_insert):
            response = self.a.post(f"{ROOT}/{series['id']}/occurrences", json=body)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.task_count(series), 0)
        self.assertEqual(self.generate(series)["created_count"], 7)

    def test_12_week_is_scheduled_saved_and_undoable_with_independent_occurrences(self):
        _, series = self.create()
        tasks = self.generate(series)["tasks"]
        availability = [{"start": f"{task['occurrence_date']}T09:00:00Z", "end": f"{task['occurrence_date']}T18:00:00Z"} for task in tasks]
        request = {"start_date": self.day.isoformat(), "days": 7, "task_ids": [task["id"] for task in tasks], "availability": availability}
        response = self.a.post("/api/v1/schedules/preview", json=request)
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()
        self.assertEqual(len(preview["blocks"]), 7)
        dates = {task["id"]: task["occurrence_date"] for task in tasks}
        for block in preview["blocks"]:
            self.assertEqual(block["start"][:10], dates[block["task_id"]])
        content = {**request, "blocks": preview["blocks"]}
        save = {"client_request_id": str(uuid4()), "title": "هفتهٔ انگلیسی", "content": content,
                "task_versions": preview["task_versions"], "preference_version": preview["preference_version"]}
        response = self.a.post("/api/v1/schedules", json=save)
        self.assertEqual(response.status_code, 201, response.text)
        saved = response.json()["schedule"]
        path = f"/api/v1/schedules/{saved['id']}"
        self.replace(series, title="فقط نوبت‌های جدید")
        self.assertFalse(self.a.get(path).json()["sources"]["stale"])
        edit = copy.deepcopy(save)
        edit.update(client_request_id=str(uuid4()), expected_version=1)
        block = edit["content"]["blocks"][0]
        block.update(start=f"{self.day}T16:00:00Z", end=f"{self.day}T17:00:00Z")
        response = self.a.put(path, json=edit)
        self.assertEqual(response.status_code, 200, response.text)
        undo = self.a.post(path + "/undo", json={"client_request_id": str(uuid4()), "expected_version": 2})
        self.assertEqual(undo.status_code, 200, undo.text)
        self.assertEqual(undo.json()["schedule"]["state"], saved["state"])
        self.assertEqual(self.task_count(series), 7)

    def test_13_database_rejects_cross_owner_and_duplicate_occurrence(self):
        _, series = self.create()
        task = self.generate(series, days=1)["tasks"][0]
        for owner, error in ((self.other_owner, psycopg.errors.ForeignKeyViolation), (self.owner, psycopg.errors.UniqueViolation)):
            with connect(DB_URL) as conn, self.assertRaises(error):
                with conn.transaction():
                    conn.execute("INSERT INTO tasks(user_id,client_request_id,title,duration_minutes,recurrence_id,occurrence_date) VALUES (%s,%s,'invalid',5,%s,%s)",
                                 (owner, uuid4(), UUID(series["id"]), task["occurrence_date"]))
        self.assertEqual(self.task_count(series), 1)

    @unittest.skipUnless(FLAVOR == "native", "Requires native PostgreSQL row-lock concurrency.")
    def test_14_parallel_generation_works_under_repeatable_read_default(self):
        _, series = self.create()
        barrier = Barrier(2)
        def generate(_):
            with connect(DB_URL) as conn:
                conn.execute("SET default_transaction_isolation='repeatable read'")
                barrier.wait(timeout=5)
                return RecurrenceRepository(conn).materialize(self.owner, UUID(series["id"]), OccurrenceRequest(expected_version=1, start_date=self.day))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(generate, range(2)))
        self.assertEqual(sorted(result.created_count for result in results), [0, 7])
        self.assertEqual(results[0].tasks, results[1].tasks)
        self.assertEqual(self.task_count(series), 7)

    @unittest.skipUnless(FLAVOR == "native", "Requires native PostgreSQL row-lock concurrency.")
    def test_15_parallel_creation_and_stale_edits(self):
        data = RecurrenceCreate(client_request_id=uuid4(), title="Concurrent", duration_minutes=60, frequency="daily", start_date=self.day)
        barrier = Barrier(2)
        def create(_):
            with connect(DB_URL) as conn:
                conn.execute("SET default_transaction_isolation='repeatable read'")
                barrier.wait(timeout=5)
                return RecurrenceRepository(conn).create(self.owner, data)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, range(2)))
        self.assertEqual(results[0][0].id, results[1][0].id)
        self.assertEqual(sorted(result[1] for result in results), [False, True])
        record = results[0][0]
        def edit(duration):
            with connect(DB_URL) as conn:
                barrier.wait(timeout=5)
                try:
                    RecurrenceRepository(conn).replace(self.owner, record.id, RecurrenceReplace(expected_version=1, values={**self.values(record.model_dump()), "duration_minutes": duration}))
                    return 200
                except AppError as exc:
                    return exc.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(edit, (30, 90))), [200, 409])

    @unittest.skipUnless(FLAVOR == "native", "Requires native PostgreSQL row-lock concurrency.")
    def test_16_update_and_delete_commit_before_waiting_generation(self):
        for action in ("update", "delete"):
            _, series = self.create()
            ready = Event()
            original = RecurrenceRepository._get
            def signal_get(repo, *args, **kwargs):
                ready.set()
                return original(repo, *args, **kwargs)
            def generate():
                with connect(DB_URL) as conn:
                    try:
                        return RecurrenceRepository(conn).materialize(self.owner, UUID(series["id"]), OccurrenceRequest(expected_version=1, start_date=self.day))
                    except AppError as exc:
                        return exc.status
            with connect(DB_URL) as conn, ThreadPoolExecutor(max_workers=1) as pool:
                with conn.transaction():
                    if action == "update":
                        conn.execute("UPDATE recurring_activities SET active=false,version=2 WHERE id=%s", (UUID(series["id"]),))
                    else:
                        conn.execute("UPDATE recurring_activities SET deleted_at=now(),version=2 WHERE id=%s", (UUID(series["id"]),))
                    with patch.object(RecurrenceRepository, "_get", signal_get):
                        future = pool.submit(generate)
                        self.assertTrue(ready.wait(timeout=5))
                self.assertEqual(future.result(timeout=5), 409 if action == "update" else 404)
            self.assertEqual(self.task_count(series), 0)


if __name__ == "__main__":
    unittest.main()
