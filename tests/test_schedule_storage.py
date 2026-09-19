"""Saved schedule acceptance tests; native-only cases exercise real row locks."""

import copy
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import patch
from uuid import UUID, uuid4

DB_URL = os.getenv("SMARTPLANNER_TEST_DATABASE_URL")
FLAVOR = os.getenv("SMARTPLANNER_TEST_DB_FLAVOR", "native")
ORIGIN = "http://localhost:8000"
PASSWORD = "Schedule history only 2026!"

if DB_URL:
    import psycopg
    from fastapi.testclient import TestClient
    from smartplanner.api import create_app
    from smartplanner.database import connect
    from smartplanner.errors import AppError
    from smartplanner.migrate import migrate
    from smartplanner.schedule_models import ScheduleCreate, ScheduleReplace
    from smartplanner.schedule_repository import ScheduleRepository
    from smartplanner.settings import Settings


@unittest.skipUnless(DB_URL, "Select a disposable PostgreSQL database.")
class ScheduleStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate(DB_URL, Path("db/migrations"))
        cls.app = create_app(Settings(DB_URL, auth_requests_per_minute=1000))
        cls.a, cls.b = TestClient(cls.app, base_url=ORIGIN), TestClient(cls.app, base_url=ORIGIN)
        for client in (cls.a, cls.b):
            email = f"schedule-{uuid4().hex}@example.com"
            response = client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD, "timezone": "UTC"})
            assert response.status_code == 201, response.text
            response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
            assert response.status_code == 200, response.text
            client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        cls.owner = UUID(cls.a.get("/api/v1/me").json()["id"])
        cls.day_counter = 0

    @classmethod
    def tearDownClass(cls):
        cls.a.close()
        cls.b.close()

    def setUp(self):
        type(self).day_counter += 1
        self.day = datetime.now(timezone.utc).date() + timedelta(days=14 * self.day_counter)

    def at(self, hour, minute=0, day=None):
        return datetime.combine(day or self.day, datetime.min.time(), timezone.utc).replace(hour=hour, minute=minute).isoformat()

    def task(self, client=None, **changes):
        response = (client or self.a).post("/api/v1/tasks", json={"client_request_id": str(uuid4()), "title": "انگلیسی", "duration_minutes": 60, **changes})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def draft(self, task=None, client=None, day=None, locked=False):
        client = client or self.a
        task = task or self.task(client)
        return {"client_request_id": str(uuid4()), "title": "برنامهٔ روز",
                "content": {"start_date": (day or self.day).isoformat(), "days": 1, "task_ids": [task["id"]],
                "availability": [{"start": self.at(9, day=day), "end": self.at(18, day=day)}], "fixed_events": [],
                "blocks": [{"task_id": task["id"], "start": self.at(9, day=day), "end": self.at(10, day=day), "locked": locked}]},
                "task_versions": {task["id"]: task["version"]}, "preference_version": client.get("/api/v1/me/preferences").json()["version"]}

    def save(self, data=None):
        data = data or self.draft()
        response = self.a.post("/api/v1/schedules", json=data)
        self.assertEqual(response.status_code, 201, response.text)
        return data, response.json()["schedule"]

    def edit_body(self, saved, **changes):
        state = saved["state"]
        body = {"client_request_id": str(uuid4()), "expected_version": saved["version"], "title": state["title"],
                "content": copy.deepcopy(state["content"]), "task_versions": dict(state["task_versions"]),
                "preference_version": state["preference_version"]}
        body.update(changes)
        return body

    def edit(self, saved, data):
        return self.a.put(f"/api/v1/schedules/{saved['id']}", json=data)

    def moved(self, saved, hour=11):
        data = self.edit_body(saved)
        data["content"]["blocks"][0].update(start=self.at(hour), end=self.at(hour + 1))
        return data

    def travel(self, saved, direction, data=None):
        return self.a.post(f"/api/v1/schedules/{saved['id']}/{direction}", json=data or {"client_request_id": str(uuid4()), "expected_version": saved["version"]})

    def get(self, saved):
        response = self.a.get(f"/api/v1/schedules/{saved['id']}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def counts(self, saved):
        with connect(DB_URL) as conn:
            return tuple(conn.execute(f"SELECT count(*) AS n FROM {table} WHERE schedule_id=%s", (UUID(saved["id"]),)).fetchone()["n"]
                         for table in ("schedule_versions", "schedule_commands", "schedule_allocations"))

    def test_01_save_preview_and_read_from_new_app_without_drift(self):
        data = self.draft()
        inputs = {key: value for key, value in data["content"].items() if key != "blocks"}
        preview = self.a.post("/api/v1/schedules/preview", json=inputs)
        self.assertEqual(preview.status_code, 200, preview.text)
        data["content"]["blocks"] = preview.json()["blocks"]
        data["task_versions"] = preview.json()["task_versions"]
        data["preference_version"] = preview.json()["preference_version"]
        _, saved = self.save(data)
        self.assertTrue(saved["persisted"])
        self.assertFalse(saved["sources"]["stale"])
        self.assertEqual(saved["state"]["content"]["blocks"], preview.json()["blocks"])
        self.assertEqual(self.get(saved), saved)
        with TestClient(create_app(Settings(DB_URL)), base_url=ORIGIN, cookies=self.a.cookies) as client:
            self.assertEqual(client.get(f"/api/v1/schedules/{saved['id']}").json(), saved)
        self.assertIn(saved["id"], [row["id"] for row in self.a.get("/api/v1/schedules", params={"limit": 100}).json()])
        history = self.a.get(f"/api/v1/schedules/{saved['id']}/history").json()
        self.assertEqual([(row["revision"], row["current"]) for row in history], [(1, True)])

    def test_02_move_undo_redo_preserve_task_and_monotonic_version(self):
        data, original = self.save()
        task_id = data["content"]["task_ids"][0]
        task_before = self.a.get(f"/api/v1/tasks/{task_id}").json()
        moved = self.edit(original, self.moved(original)).json()["schedule"]
        undone = self.travel(moved, "undo").json()["schedule"]
        redone = self.travel(undone, "redo").json()["schedule"]
        self.assertEqual([item["version"] for item in (original, moved, undone, redone)], [1, 2, 3, 4])
        self.assertEqual([item["history_revision"] for item in (original, moved, undone, redone)], [1, 2, 1, 2])
        self.assertEqual(undone["state"], original["state"])
        self.assertEqual(redone["state"], moved["state"])
        self.assertFalse(original["can_undo"])
        self.assertTrue(undone["can_redo"])
        self.assertEqual(self.a.get(f"/api/v1/tasks/{task_id}").json(), task_before)

    def test_03_creation_retry_returns_latest_state_without_reapplying(self):
        data, saved = self.save()
        moved = self.edit(saved, self.moved(saved)).json()["schedule"]
        replay = self.a.post("/api/v1/schedules", json=data)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(replay.json()["applied_version"], 1)
        self.assertEqual(replay.json()["schedule"], moved)
        self.assertEqual(self.counts(saved), (2, 2, 1))
        conflict = self.a.post("/api/v1/schedules", json={**data, "title": "different"})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["code"], "request_id_conflict")

    def test_04_edit_and_history_retries_do_not_apply_twice(self):
        _, original = self.save()
        command = self.moved(original)
        moved = self.edit(original, command).json()["schedule"]
        replay = self.edit(original, command)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["replayed"])
        undo = {"client_request_id": str(uuid4()), "expected_version": moved["version"]}
        undone = self.travel(moved, "undo", undo).json()["schedule"]
        replay = self.travel(moved, "undo", undo)
        self.assertEqual(replay.json()["schedule"], undone)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(self.travel(undone, "redo", undo).json()["code"], "request_id_conflict")
        changed_guard = {**command, "expected_version": undone["version"]}
        self.assertEqual(self.edit(undone, changed_guard).json()["code"], "request_id_conflict")

    def test_05_stale_page_cannot_write_even_after_undo_restores_same_layout(self):
        _, original = self.save()
        moved = self.edit(original, self.moved(original)).json()["schedule"]
        before = self.get(moved)
        self.assertEqual(self.edit(original, self.moved(original, 13)).status_code, 409)
        self.assertEqual(self.get(moved), before)
        undone = self.travel(moved, "undo").json()["schedule"]
        self.assertEqual(undone["state"], original["state"])
        self.assertEqual(self.edit(original, self.moved(original, 13)).json()["code"], "stale_schedule_version")

    def test_06_edit_after_undo_invalidates_redo_but_old_receipt_survives(self):
        _, original = self.save()
        old_command = self.moved(original)
        moved = self.edit(original, old_command).json()["schedule"]
        undone = self.travel(moved, "undo").json()["schedule"]
        latest = self.edit(undone, self.moved(undone, 13)).json()["schedule"]
        self.assertFalse(latest["can_redo"])
        self.assertEqual(self.travel(latest, "redo").json()["code"], "history_boundary")
        history = self.a.get(f"/api/v1/schedules/{original['id']}/history").json()
        self.assertEqual([item["revision"] for item in history], [1, 4])
        replay = self.edit(original, old_command).json()
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["applied_version"], 2)
        self.assertEqual(replay["schedule"], latest)

    def test_07_history_is_bounded_without_losing_old_request_receipts(self):
        original_request, saved = self.save()
        for index in range(22):
            response = self.edit(saved, self.edit_body(saved, title=f"برنامه {index}"))
            self.assertEqual(response.status_code, 200, response.text)
            saved = response.json()["schedule"]
        self.assertEqual(self.counts(saved), (20, 23, 1))
        replay = self.a.post("/api/v1/schedules", json=original_request).json()
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["schedule"]["version"], 23)
        for _ in range(19):
            saved = self.travel(saved, "undo").json()["schedule"]
        self.assertFalse(saved["can_undo"])
        before = self.get(saved)
        self.assertEqual(self.travel(saved, "undo").json()["code"], "history_boundary")
        self.assertEqual(self.get(saved), before)

    def test_08_all_saved_routes_are_owner_scoped(self):
        data, saved = self.save()
        path = f"/api/v1/schedules/{saved['id']}"
        self.assertEqual(self.b.get(path).status_code, 404)
        self.assertEqual(self.b.get(path + "/history").status_code, 404)
        self.assertEqual(self.b.put(path, json=self.edit_body(saved)).status_code, 404)
        for direction in ("undo", "redo"):
            self.assertEqual(self.b.post(path + "/" + direction, json={"client_request_id": str(uuid4()), "expected_version": 1}).status_code, 404)
        self.assertNotIn(saved["id"], [item["id"] for item in self.b.get("/api/v1/schedules").json()])
        self.assertEqual(self.b.post("/api/v1/schedules", json=data).status_code, 409)
        other = self.draft(client=self.b)
        other["client_request_id"] = data["client_request_id"]
        self.assertEqual(self.b.post("/api/v1/schedules", json=other).status_code, 201)
        self.assertEqual(self.get(saved), saved)

    def test_09_new_routes_require_session_csrf_and_reject_owner_injection(self):
        data, saved = self.save()
        path = f"/api/v1/schedules/{saved['id']}"
        with TestClient(self.app, base_url=ORIGIN) as client:
            for suffix in ("", f"/{saved['id']}", f"/{saved['id']}/history"):
                self.assertEqual(client.get("/api/v1/schedules" + suffix).status_code, 401)
            self.assertEqual(client.post("/api/v1/schedules", json=data).status_code, 401)
        bad = {"X-CSRF-Token": "wrong"}
        self.assertEqual(self.a.post("/api/v1/schedules", json=data, headers=bad).status_code, 403)
        self.assertEqual(self.a.put(path, json=self.edit_body(saved), headers=bad).status_code, 403)
        for direction in ("undo", "redo"):
            self.assertEqual(self.a.post(path + "/" + direction, json={"client_request_id": str(uuid4()), "expected_version": 1}, headers=bad).status_code, 403)
        self.assertEqual(self.a.post("/api/v1/schedules", json={**data, "user_id": str(uuid4())}).status_code, 422)

    def test_10_changed_task_requires_fresh_layout_and_prevents_old_undo(self):
        data, original = self.save()
        task_id = data["content"]["task_ids"][0]
        self.assertEqual(self.a.patch(f"/api/v1/tasks/{task_id}", json={"expected_version": 1, "duration_minutes": 90}).status_code, 200)
        stale = self.get(original)
        self.assertEqual(stale["sources"]["changed_task_ids"], [task_id])
        self.assertTrue(stale["sources"]["stale"])
        command = self.edit_body(original)
        self.assertEqual(self.edit(original, command).json()["code"], "schedule_sources_changed")
        command["task_versions"][task_id] = 2
        self.assertEqual(self.edit(original, command).status_code, 409)
        command["content"]["blocks"][0]["end"] = self.at(10, 30)
        changed = self.edit(original, command)
        self.assertEqual(changed.status_code, 200, changed.text)
        saved = changed.json()["schedule"]
        self.assertFalse(saved["sources"]["stale"])
        self.assertEqual(self.travel(saved, "undo").json()["code"], "schedule_sources_changed")
        self.assertEqual(self.get(saved), saved)

    def test_11_changed_preferences_mark_saved_plan_stale_and_block_old_undo(self):
        _, original = self.save()
        version = original["state"]["preference_version"]
        changed = self.a.put("/api/v1/me/preferences", json={"expected_version": version, "values": {"break_minutes": 0}})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertTrue(self.get(original)["sources"]["preferences_changed"])
        self.assertEqual(self.edit(original, self.edit_body(original)).status_code, 409)
        updated = self.edit(original, self.edit_body(original, preference_version=changed.json()["version"])).json()["schedule"]
        self.assertEqual(self.travel(updated, "undo").json()["code"], "schedule_sources_changed")

    def test_12_completed_locked_task_can_be_removed_but_not_restored(self):
        data, saved = self.save(self.draft(locked=True))
        task_id = data["content"]["task_ids"][0]
        self.a.patch(f"/api/v1/tasks/{task_id}", json={"expected_version": 1, "status": "completed"})
        command = self.edit_body(saved)
        command["task_versions"][task_id] = 2
        self.assertEqual(self.edit(saved, command).json()["code"], "inactive_task_block")
        command["content"]["blocks"] = []
        removed = self.edit(saved, command).json()["schedule"]
        self.assertEqual(removed["state"]["ignored_task_ids"], [task_id])
        self.assertEqual(self.travel(removed, "undo").json()["code"], "schedule_sources_changed")

    def test_13_deleted_task_is_flagged_and_cannot_be_resurrected_by_history(self):
        data, saved = self.save(self.draft(locked=True))
        task_id = data["content"]["task_ids"][0]
        self.assertEqual(self.a.delete(f"/api/v1/tasks/{task_id}", params={"expected_version": 1}).status_code, 204)
        self.assertEqual(self.get(saved)["sources"]["missing_task_ids"], [task_id])
        command = self.edit_body(saved, task_versions={})
        command["content"].update(task_ids=[], blocks=[])
        removed = self.edit(saved, command).json()["schedule"]
        self.assertEqual(self.travel(removed, "undo").json()["code"], "schedule_sources_changed")
        self.assertEqual(self.a.get(f"/api/v1/tasks/{task_id}").status_code, 404)

    def test_14_locked_task_cannot_move_or_disappear_until_separate_unlock(self):
        _, saved = self.save(self.draft(locked=True))
        moved = self.moved(saved)
        moved["content"]["blocks"][0]["locked"] = False
        self.assertEqual(self.edit(saved, moved).json()["code"], "locked_block_conflict")
        omitted = self.edit_body(saved, task_versions={})
        omitted["content"].update(task_ids=[], blocks=[])
        self.assertEqual(self.edit(saved, omitted).json()["code"], "locked_block_conflict")
        unlock = self.edit_body(saved)
        unlock["content"]["blocks"][0]["locked"] = False
        unlocked = self.edit(saved, unlock).json()["schedule"]
        self.assertEqual(self.edit(unlocked, self.moved(unlocked)).status_code, 200)

    def test_15_manual_payload_cannot_bypass_duration_availability_or_fixed_event(self):
        data = self.draft()
        variants = []
        too_short = copy.deepcopy(data)
        too_short["content"]["blocks"][0]["end"] = self.at(9, 30)
        variants.append(too_short)
        unavailable = copy.deepcopy(data)
        unavailable["content"]["availability"] = []
        variants.append(unavailable)
        conflict = copy.deepcopy(data)
        conflict["content"]["fixed_events"] = [{"title": "class", "start": self.at(9), "end": self.at(10)}]
        variants.append(conflict)
        duplicate = copy.deepcopy(data)
        duplicate["content"]["blocks"] *= 2
        variants.append(duplicate)
        for variant in variants:
            response = self.a.post("/api/v1/schedules", json=variant)
            self.assertEqual(response.status_code, 409, response.text)
        # Failed attempts did not consume the request ID or save a partial row.
        self.save(data)

    def test_16_manual_payload_must_respect_stored_task_deadline(self):
        item = self.task(deadline=self.at(10))
        data = self.draft(item)
        data["content"]["blocks"][0].update(start=self.at(11), end=self.at(12))
        self.assertEqual(self.a.post("/api/v1/schedules", json=data).status_code, 409)

    def test_17_started_blocks_stay_but_title_edit_remains_possible(self):
        _, saved = self.save()
        with patch("smartplanner.schedule_repository.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime.fromisoformat(self.at(10, 30))
            self.assertEqual(self.edit(saved, self.moved(saved)).json()["code"], "started_block_conflict")
            omitted = self.edit_body(saved)
            omitted["content"]["blocks"] = []
            self.assertEqual(self.edit(saved, omitted).json()["code"], "started_block_conflict")
            response = self.edit(saved, self.edit_body(saved, title="عنوان تازه"))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["schedule"]["state"]["content"]["blocks"], saved["state"]["content"]["blocks"])

    def test_18_undo_cannot_create_new_past_work(self):
        _, saved = self.save()
        moved = self.edit(saved, self.moved(saved)).json()["schedule"]
        with patch("smartplanner.schedule_repository.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime.fromisoformat(self.at(10, 30))
            self.assertEqual(self.travel(moved, "undo").json()["code"], "past_block")
        self.assertEqual(self.get(moved), moved)

    def test_19_partial_split_preview_retains_remaining_duration(self):
        item = self.task(duration_minutes=90, splittable=True)
        data = self.draft(item, locked=True)
        data["content"]["blocks"][0]["end"] = self.at(9, 45)
        data["content"]["availability"][0]["end"] = self.at(9, 45)
        _, saved = self.save(data)
        self.assertEqual(saved["state"]["unscheduled"][0]["remaining_minutes"], 45)
        self.assertEqual(saved["state"]["unscheduled"][0]["reason"], "not_scheduled")

    def test_20_plan_horizons_cannot_overlap_but_adjacent_plans_can_exist(self):
        data = self.draft()
        data["content"]["days"] = 7
        self.save(data)
        conflict = self.a.post("/api/v1/schedules", json=self.draft(day=self.day + timedelta(days=1)))
        self.assertEqual(conflict.json()["code"], "schedule_overlap")
        self.save(self.draft(day=self.day + timedelta(days=7)))

    def test_21_task_cannot_be_assigned_to_two_plans_even_by_undo(self):
        item = self.task()
        _, first = self.save(self.draft(item))
        second_request = self.draft(item, day=self.day + timedelta(days=1))
        self.assertEqual(self.a.post("/api/v1/schedules", json=second_request).json()["code"], "task_already_scheduled")
        unassign = self.edit_body(first)
        unassign["content"]["blocks"] = []
        first = self.edit(first, unassign).json()["schedule"]
        _, second = self.save(second_request)
        self.assertEqual(self.travel(first, "undo").json()["code"], "task_already_scheduled")
        self.assertEqual(self.get(first), first)
        self.assertEqual(self.counts(first)[2], 0)
        self.assertEqual(self.counts(second)[2], 1)
        unassign = self.edit_body(second)
        unassign["content"]["blocks"] = []
        self.assertEqual(self.edit(second, unassign).status_code, 200)
        self.assertEqual(self.travel(first, "undo").status_code, 200)

    def test_22_failure_after_writes_rolls_back_cursor_history_allocations_and_receipt(self):
        _, saved = self.save()
        command = self.moved(saved)
        before = self.counts(saved)
        with patch.object(ScheduleRepository, "_finish", side_effect=AppError(503, "test_failure", "test")):
            self.assertEqual(self.edit(saved, command).status_code, 503)
        self.assertEqual(self.get(saved), saved)
        self.assertEqual(self.counts(saved), before)
        self.assertEqual(self.edit(saved, command).status_code, 200)

    def test_23_create_failure_does_not_leave_plan_or_consume_retry_id(self):
        data = self.draft()
        with patch.object(ScheduleRepository, "_finish", side_effect=AppError(503, "test_failure", "test")):
            self.assertEqual(self.a.post("/api/v1/schedules", json=data).status_code, 503)
        _, saved = self.save(data)
        self.assertEqual(self.counts(saved), (1, 1, 1))

    def test_24_horizon_change_and_crossing_fixed_event_are_explicitly_rejected(self):
        _, saved = self.save()
        changed = self.edit_body(saved)
        changed["content"]["days"] = 7
        self.assertEqual(self.edit(saved, changed).json()["code"], "schedule_horizon_changed")
        changed = self.edit_body(saved)
        changed["content"]["fixed_events"] = [{"title": "overnight", "start": self.at(23), "end": self.at(10, day=self.day + timedelta(days=1))}]
        self.assertEqual(self.edit(saved, changed).json()["code"], "fixed_event_outside_saved_horizon")
        self.assertEqual(self.get(saved), saved)

    def test_25_weekly_storage_preserves_cross_midnight_times(self):
        data = self.draft()
        data["content"]["days"] = 7
        event = {"title": "overnight", "start": self.at(23), "end": self.at(10, day=self.day + timedelta(days=1))}
        data["content"]["fixed_events"] = [event]
        _, saved = self.save(data)
        stored = saved["state"]["content"]["fixed_events"][0]
        self.assertEqual(datetime.fromisoformat(stored["end"]) - datetime.fromisoformat(stored["start"]), timedelta(hours=11))

    def test_26_contract_rejects_forged_results_missing_versions_and_boolean_guard(self):
        data = self.draft()
        for variant in ({**data, "task_versions": {}}, {**data, "persisted": True}, {**data, "unscheduled": []},
                        {**data, "preference_version": True}):
            self.assertEqual(self.a.post("/api/v1/schedules", json=variant).status_code, 422)
        _, saved = self.save(data)
        self.assertEqual(self.edit(saved, self.edit_body(saved, expected_version=True)).status_code, 422)

    @unittest.skipUnless(FLAVOR == "native", "Concurrent writers require native PostgreSQL.")
    def test_27_concurrent_stale_edits_only_one_commits(self):
        _, saved = self.save()
        barrier = Barrier(2)
        def edit(hour):
            data = ScheduleReplace.model_validate(self.moved(saved, hour))
            with connect(DB_URL) as conn:
                barrier.wait(timeout=5)
                try:
                    return ScheduleRepository(conn).replace(self.owner, UUID(saved["id"]), data).schedule.version
                except AppError as exc:
                    return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, [11, 13]))
        self.assertCountEqual(results, [2, "stale_schedule_version"])
        self.assertEqual(self.counts(saved), (2, 2, 1))

    @unittest.skipUnless(FLAVOR == "native", "Concurrent writers require native PostgreSQL.")
    def test_28_concurrent_duplicate_create_and_overlapping_horizon(self):
        request = ScheduleCreate.model_validate(self.draft())
        barrier = Barrier(2)
        original = ScheduleRepository._owner_lock
        def coordinated_lock(repo, owner):
            # Establish snapshots BEFORE either owner lock. Without explicit
            # READ COMMITTED the waiter's RR snapshot misses the winner's plan.
            repo.conn.execute("SELECT 1")
            barrier.wait(timeout=5)
            return original(repo, owner)
        def create(data):
            with connect(DB_URL) as conn:
                conn.execute("SET default_transaction_isolation='repeatable read'")
                try:
                    result = ScheduleRepository(conn).create(self.owner, data)
                    return result.schedule.id, result.replayed
                except AppError as exc:
                    return exc.code
        with patch.object(ScheduleRepository, "_owner_lock", coordinated_lock), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, [request, request]))
        self.assertEqual(results[0][0], results[1][0])
        self.assertCountEqual([item[1] for item in results], [False, True])
        # Distinct requests for the same otherwise free horizon must serialize too.
        day = self.day + timedelta(days=2)
        one, two = ScheduleCreate.model_validate(self.draft(day=day)), ScheduleCreate.model_validate(self.draft(day=day))
        barrier = Barrier(2)
        with patch.object(ScheduleRepository, "_owner_lock", coordinated_lock), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, [one, two]))
        self.assertEqual(sum(isinstance(item, tuple) for item in results), 1)
        self.assertIn("schedule_overlap", results)

    @unittest.skipUnless(FLAVOR == "native", "Source row locks require native PostgreSQL.")
    def test_29_source_edit_that_commits_before_save_lock_is_detected(self):
        request = ScheduleCreate.model_validate(self.draft())
        task_id = next(iter(request.task_versions))
        entered = Event()
        original = ScheduleRepository._owner_lock
        def signal_owner(repo, owner):
            result = original(repo, owner)
            entered.set()
            return result
        def save():
            with connect(DB_URL) as conn:
                try:
                    return ScheduleRepository(conn).create(self.owner, request)
                except AppError as exc:
                    return exc.code
        with ThreadPoolExecutor(max_workers=1) as pool, patch.object(ScheduleRepository, "_owner_lock", signal_owner):
            with connect(DB_URL) as writer:
                with writer.transaction():
                    writer.execute("UPDATE tasks SET duration_minutes=90,version=version+1 WHERE id=%s", (task_id,))
                    future = pool.submit(save)
                    self.assertTrue(entered.wait(timeout=5))
            self.assertEqual(future.result(timeout=5), "schedule_sources_changed")

    @unittest.skipUnless(FLAVOR == "native", "Source row locks require native PostgreSQL.")
    def test_30_source_edits_cannot_commit_between_validation_and_save(self):
        data = self.draft()
        task_id = UUID(data["content"]["task_ids"][0])
        original = ScheduleRepository._insert_state
        def blocked_write(sql, parameters):
            with connect(DB_URL) as writer:
                with self.assertRaises(psycopg.errors.LockNotAvailable):
                    with writer.transaction():
                        writer.execute("SET LOCAL lock_timeout='150ms'")
                        writer.execute(sql, parameters)
        def inspect_while_locked(repo, *args):
            original(repo, *args)
            blocked_write("UPDATE tasks SET version=version+1 WHERE id=%s", (task_id,))
            blocked_write("UPDATE user_preferences SET version=version+1 WHERE user_id=%s", (self.owner,))
        with patch.object(ScheduleRepository, "_insert_state", inspect_while_locked):
            _, saved = self.save(data)
        self.assertFalse(saved["sources"]["stale"])
        self.assertEqual(saved["state"]["task_versions"][str(task_id)], 1)

    @unittest.skipUnless(FLAVOR == "native", "Deferred foreign-key behavior is verified on native PostgreSQL.")
    def test_31_database_will_not_commit_a_dangling_history_cursor(self):
        _, saved = self.save()
        with connect(DB_URL) as conn:
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                with conn.transaction():
                    conn.execute("UPDATE schedules SET current_revision=999 WHERE id=%s", (UUID(saved["id"]),))
        self.assertEqual(self.get(saved), saved)


if __name__ == "__main__":
    unittest.main()
