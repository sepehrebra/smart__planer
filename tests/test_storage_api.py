"""Integration tests require an explicitly selected, disposable PostgreSQL test database."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import patch
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

DB_URL = os.getenv("SMARTPLANNER_TEST_DATABASE_URL")
FLAVOR = os.getenv("SMARTPLANNER_TEST_DB_FLAVOR", "native")

if DB_URL:
    import psycopg
    from fastapi.testclient import TestClient
    from smartplanner.accounts import token_digest
    from smartplanner.api import COOKIE_NAME, create_app
    from smartplanner.database import connect
    from smartplanner.errors import AppError
    from smartplanner.migrate import migrate
    from smartplanner.models import TaskCreate, TaskPatch
    from smartplanner.repository import Repository
    from smartplanner.scheduler import build_preview
    from smartplanner.settings import Settings
    from smartplanner.task_changes import prepare_task_change

PASSWORD = "  English practice 2026!  "
ORIGIN = "http://localhost:8000"


@unittest.skipUnless(DB_URL, "Set SMARTPLANNER_TEST_DATABASE_URL to a disposable test database.")
class StorageApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate(DB_URL, Path("db/migrations"))
        cls.config = Settings(DB_URL, auth_requests_per_minute=1000)
        cls.app = create_app(cls.config)
        cls.a = TestClient(cls.app, base_url=ORIGIN)
        cls.b = TestClient(cls.app, base_url=ORIGIN)
        cls.email_a = f"a-{uuid4().hex}@example.com"
        cls.email_b = f"b-{uuid4().hex}@example.com"
        for client, email in [(cls.a, cls.email_a), (cls.b, cls.email_b)]:
            response = client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD, "timezone": "Asia/Tehran"})
            if response.status_code != 201:
                raise AssertionError(f"Registration failed: {response.status_code} {response.text}")
            response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
            if response.status_code != 200:
                raise AssertionError(f"Login failed: {response.status_code} {response.text}")
            client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        cls.owner_a = cls.a.get("/api/v1/me").json()["id"]
        cls.owner_b = cls.b.get("/api/v1/me").json()["id"]

    @classmethod
    def tearDownClass(cls):
        cls.a.close()
        cls.b.close()

    def new_task(self, **changes):
        data = {"client_request_id": str(uuid4()), "title": "انگلیسی", "duration_minutes": 60}
        data.update(changes)
        response = self.a.post("/api/v1/tasks", json=data)
        self.assertEqual(response.status_code, 201, response.text)
        return data, response.json()

    def fresh_login(self, config=None):
        client = TestClient(create_app(config or self.config), base_url=(config.origin if config else ORIGIN))
        response = client.post("/api/v1/auth/login", json={"email": self.email_a, "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return client, response

    def preview_request(self, *tasks, **changes):
        tomorrow = (datetime.now(ZoneInfo("Asia/Tehran")) + timedelta(days=1)).date().isoformat()
        data = {"start_date": tomorrow, "task_ids": [task["id"] for task in tasks],
                "availability": [{"start": f"{tomorrow}T09:00:00+03:30", "end": f"{tomorrow}T18:00:00+03:30"}]}
        data.update(changes)
        return data

    def test_01_health_schema_and_migration_replay(self):
        self.assertEqual(self.a.get("/api/v1/health").status_code, 200)
        self.assertEqual(self.a.get("/api/v1/ready").status_code, 200)
        self.assertEqual(migrate(DB_URL, Path("db/migrations")), [])

    def test_02_record_survives_new_application_and_connection(self):
        _, task = self.new_task()
        with self.fresh_login()[0] as client:
            result = client.get(f"/api/v1/tasks/{task['id']}")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json(), task)

    def test_03_another_user_cannot_read_edit_or_delete(self):
        _, task = self.new_task()
        path = f"/api/v1/tasks/{task['id']}"
        self.assertEqual(self.b.get(path).status_code, 404)
        self.assertEqual(self.b.patch(path, json={"expected_version": 1, "title": "hijacked"}).status_code, 404)
        self.assertEqual(self.b.delete(path, params={"expected_version": 1}).status_code, 404)
        self.assertNotIn(task["id"], [row["id"] for row in self.b.get("/api/v1/tasks").json()])
        self.assertEqual(self.a.get(path).json()["title"], "انگلیسی")

    def test_04_owner_injection_is_rejected(self):
        response = self.a.post("/api/v1/tasks", json={"client_request_id": str(uuid4()), "title": "English", "duration_minutes": 60, "user_id": self.owner_b})
        self.assertEqual(response.status_code, 422)

    def test_05_retry_creates_only_one_row_even_after_edit(self):
        data, task = self.new_task()
        path = f"/api/v1/tasks/{task['id']}"
        self.a.patch(path, json={"expected_version": 1, "title": "واژگان انگلیسی"})
        replay = self.a.post("/api/v1/tasks", json=data)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["id"], task["id"])
        self.assertEqual(replay.json()["title"], "واژگان انگلیسی")
        with connect(DB_URL) as conn:
            count = conn.execute("SELECT count(*) AS n FROM tasks WHERE user_id=%s AND client_request_id=%s", (UUID(self.owner_a), UUID(data["client_request_id"]))).fetchone()["n"]
        self.assertEqual(count, 1)

    def test_06_reused_request_with_different_content_is_conflict(self):
        data, task = self.new_task()
        data["duration_minutes"] = 90
        self.assertEqual(self.a.post("/api/v1/tasks", json=data).status_code, 409)

    def test_07_request_identifiers_are_scoped_to_user(self):
        data, task = self.new_task()
        other = self.b.post("/api/v1/tasks", json=data)
        self.assertEqual(other.status_code, 201)
        self.assertNotEqual(other.json()["id"], task["id"])

    def test_08_stale_update_cannot_overwrite_new_data(self):
        _, task = self.new_task()
        path = f"/api/v1/tasks/{task['id']}"
        first = self.a.patch(path, json={"expected_version": 1, "duration_minutes": 90})
        second = self.a.patch(path, json={"expected_version": 1, "duration_minutes": 30})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        saved = self.a.get(path).json()
        self.assertEqual((saved["duration_minutes"], saved["version"]), (90, 2))

    def test_09_invalid_combined_edit_does_not_change_saved_record(self):
        _, task = self.new_task(earliest_start="2026-09-20T10:00:00Z", deadline="2026-09-20T11:00:00Z")
        path = f"/api/v1/tasks/{task['id']}"
        rejected = self.a.patch(path, json={"expected_version": 1, "duration_minutes": 90})
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(self.a.get(path).json(), task)

    def test_10_delete_cannot_be_undone_by_late_create_retry(self):
        data, task = self.new_task()
        path = f"/api/v1/tasks/{task['id']}"
        self.assertEqual(self.a.delete(path, params={"expected_version": 1}).status_code, 204)
        self.assertEqual(self.a.get(path).status_code, 404)
        self.assertEqual(self.a.post("/api/v1/tasks", json=data).status_code, 409)

    def test_11_stale_delete_preserves_newer_task(self):
        _, task = self.new_task()
        path = f"/api/v1/tasks/{task['id']}"
        self.a.patch(path, json={"expected_version": 1, "status": "completed"})
        self.assertEqual(self.a.delete(path, params={"expected_version": 1}).status_code, 409)
        self.assertEqual(self.a.get(path).json()["status"], "completed")

    def test_12_database_rejects_bad_duration_and_rolls_back_whole_transaction(self):
        request_id = uuid4()
        with connect(DB_URL) as conn:
            with self.assertRaises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute("INSERT INTO tasks(user_id, client_request_id, title, duration_minutes) VALUES (%s,%s,%s,%s)", (UUID(self.owner_a), request_id, "rollback probe", 60))
                    conn.execute("UPDATE tasks SET duration_minutes=-1 WHERE user_id=%s AND client_request_id=%s", (UUID(self.owner_a), request_id))
        # Each API request closes its connection. Check what the next request sees,
        # using a fresh connection, rather than relying on reuse after a SQL error.
        with connect(DB_URL) as conn:
            count = conn.execute("SELECT count(*) AS n FROM tasks WHERE client_request_id=%s", (request_id,)).fetchone()["n"]
            self.assertEqual(count, 0)

    def test_13_sql_like_title_is_stored_as_text(self):
        title = "English'); DROP TABLE users; --"
        _, task = self.new_task(title=title)
        self.assertEqual(task["title"], title)
        self.assertEqual(self.a.get("/api/v1/me").status_code, 200)

    def test_14_preferences_are_isolated_and_versioned(self):
        before = self.a.get("/api/v1/me/preferences").json()
        other_before = self.b.get("/api/v1/me/preferences").json()
        data = {"expected_version": before["version"], "values": {"workload": "light", "focus_block_minutes": 25}}
        first = self.a.put("/api/v1/me/preferences", json=data)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["focus_block_minutes"], 25)
        self.assertEqual(self.a.put("/api/v1/me/preferences", json=data).status_code, 409)
        self.assertEqual(self.b.get("/api/v1/me/preferences").json(), other_before)

    def test_15_authentication_required_and_malformed_cookie_safe(self):
        with TestClient(self.app, base_url=ORIGIN) as client:
            self.assertEqual(client.get("/api/v1/tasks").status_code, 401)
            client.cookies.set(COOKIE_NAME, "invalid")
            self.assertEqual(client.get("/api/v1/tasks").status_code, 401)

    def test_16_csrf_and_foreign_origin_rejected_without_change(self):
        _, task = self.new_task()
        path = f"/api/v1/tasks/{task['id']}"
        body = {"expected_version": 1, "duration_minutes": 10}
        self.assertEqual(self.a.patch(path, json=body, headers={"X-CSRF-Token": "wrong"}).status_code, 403)
        self.assertEqual(self.a.patch(path, json=body, headers={"Origin": "https://other.example"}).status_code, 403)
        self.assertEqual(self.a.get(path).json()["version"], 1)
        self.assertEqual(self.a.post("/api/v1/auth/login", json={"email": self.email_a, "password": PASSWORD}, headers={"Origin": "https://other.example"}).status_code, 403)

    def test_17_logout_invalidates_copied_session(self):
        with self.fresh_login()[0] as client:
            stolen = client.cookies.get(COOKIE_NAME)
            self.assertEqual(client.post("/api/v1/auth/logout").status_code, 204)
            client.cookies.clear()
            client.cookies.set(COOKIE_NAME, stolen)
            self.assertEqual(client.get("/api/v1/me").status_code, 401)

    def test_18_expired_session_is_rejected(self):
        with self.fresh_login()[0] as client:
            digest = token_digest(client.cookies.get(COOKIE_NAME))
            with connect(DB_URL) as conn:
                conn.execute("UPDATE sessions SET created_at=now()-INTERVAL '2 days', expires_at=now()-INTERVAL '1 day' WHERE token_digest=%s", (digest,))
            self.assertEqual(client.get("/api/v1/me").status_code, 401)

    def test_19_password_whitespace_is_preserved_and_hash_is_not_exposed(self):
        bad = self.a.post("/api/v1/auth/login", json={"email": self.email_a, "password": PASSWORD.strip()})
        self.assertEqual(bad.status_code, 401)
        with connect(DB_URL) as conn:
            value = conn.execute("SELECT password_hash FROM users WHERE id=%s", (UUID(self.owner_a),)).fetchone()["password_hash"]
        self.assertTrue(value.startswith("$argon2id$"))
        self.assertNotIn(PASSWORD, value)
        self.assertNotIn("password", self.a.get("/api/v1/me").text)

    def test_20_invalid_password_is_not_echoed_in_validation_error(self):
        sensitive = "short-key"
        response = self.a.post("/api/v1/auth/register", json={"email": "test@example.com", "password": sensitive})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(sensitive, response.text)

    def test_21_email_case_policy_and_timezone_validation(self):
        duplicate = self.a.post("/api/v1/auth/register", json={"email": self.email_a.upper(), "password": PASSWORD})
        self.assertEqual(duplicate.status_code, 409)
        bad_timezone = self.a.post("/api/v1/auth/register", json={"email": f"new-{uuid4().hex}@example.com", "password": PASSWORD, "timezone": "Mars/Base"})
        self.assertEqual(bad_timezone.status_code, 422)

    def test_22_session_and_cookie_security_contract(self):
        config = Settings(DB_URL, origin="https://localhost:8000", environment="production", cookie_secure=True)
        client, login = self.fresh_login(config)
        with client:
            cookie = login.headers["set-cookie"].lower()
            self.assertIn("httponly", cookie)
            self.assertIn("secure", cookie)
            self.assertIn("samesite=lax", cookie)
            self.assertEqual(client.get("/api/v1/auth/session").json()["csrf_token"], login.json()["csrf_token"])
            self.assertEqual(client.get("/api/v1/me").headers["cache-control"], "no-store")

    def test_23_authentication_attempts_are_limited(self):
        config = Settings(DB_URL, auth_requests_per_minute=2)
        with TestClient(create_app(config), base_url=ORIGIN) as client:
            body = {"email": self.email_a, "password": "a-wrong-password-2026"}
            self.assertEqual([client.post("/api/v1/auth/login", json=body).status_code for _ in range(3)], [401, 401, 429])

    def test_24_storage_outage_returns_safe_error_but_liveness_works(self):
        config = Settings("postgresql://postgres@127.0.0.1:1/postgres?sslmode=disable")
        with TestClient(create_app(config), base_url=ORIGIN) as client:
            self.assertEqual(client.get("/api/v1/health").status_code, 200)
            response = client.get("/api/v1/ready")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("postgresql://", response.text)

    @unittest.skipUnless(FLAVOR == "native", "Concurrent transaction behavior requires native PostgreSQL.")
    def test_25_concurrent_stale_updates_only_one_commits(self):
        _, task = self.new_task()
        barrier = Barrier(2)
        def coordinated_prepare(*args, **kwargs):
            result = prepare_task_change(*args, **kwargs)
            barrier.wait(timeout=5)
            return result
        def edit(duration):
            with connect(DB_URL) as conn:
                try:
                    Repository(conn).update_task(UUID(self.owner_a), UUID(task["id"]), TaskPatch(expected_version=1, duration_minutes=duration))
                    return 200
                except AppError as exc:
                    return exc.status
        with patch("smartplanner.repository.prepare_task_change", coordinated_prepare):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(edit, [90, 30]))
        self.assertEqual(sorted(results), [200, 409])

    @unittest.skipUnless(FLAVOR == "native", "Concurrent transaction behavior requires native PostgreSQL.")
    def test_26_concurrent_duplicate_creates_only_one_task(self):
        data = TaskCreate(client_request_id=uuid4(), title="concurrent create", duration_minutes=60)
        barrier = Barrier(2)
        def create(_):
            with connect(DB_URL) as conn:
                barrier.wait(timeout=5)
                task, replayed = Repository(conn).create_task(UUID(self.owner_a), data)
                return task.id, replayed
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, [0, 1]))
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(sorted(result[1] for result in results), [False, True])

    @unittest.skipUnless(FLAVOR == "native", "PGlite Socket has a known response-sync issue after a bound SQL error.")
    def test_27_connection_reusable_after_bound_sql_error(self):
        with connect(DB_URL) as conn:
            with self.assertRaises(psycopg.errors.DivisionByZero):
                with conn.transaction():
                    conn.execute("SELECT 1 / %s", (0,))
            self.assertEqual(conn.execute("SELECT 1 AS n").fetchone()["n"], 1)

    def test_28_preview_uses_saved_inputs_without_changing_them(self):
        _, task = self.new_task()
        preferences = self.a.get("/api/v1/me/preferences").json()
        data = self.preview_request(task)
        response = self.a.post("/api/v1/schedules/preview", json=data)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertFalse(result["persisted"])
        self.assertEqual(result["timezone"], "Asia/Tehran")
        self.assertEqual(result["task_versions"], {task["id"]: 1})
        self.assertEqual(result["preference_version"], preferences["version"])
        self.assertEqual(len(result["blocks"]), 1)
        placed = result["blocks"][0]
        self.assertEqual(placed["task_id"], task["id"])
        self.assertEqual(datetime.fromisoformat(placed["end"]) - datetime.fromisoformat(placed["start"]), timedelta(hours=1))
        self.assertEqual(result["unscheduled"], [])
        self.assertEqual(self.a.get(f"/api/v1/tasks/{task['id']}").json(), task)
        self.assertEqual(self.a.get("/api/v1/me/preferences").json(), preferences)
        repeated = self.a.post("/api/v1/schedules/preview", json=data).json()
        self.assertEqual(repeated["blocks"], result["blocks"])

    def test_29_empty_selection_still_returns_fixed_events(self):
        data = self.preview_request()
        data["fixed_events"] = [{"title": "دانشگاه", **data["availability"][0]}]
        response = self.a.post("/api/v1/schedules/preview", json=data)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["blocks"], [])
        self.assertEqual(result["task_versions"], {})
        self.assertEqual(result["fixed_events"][0]["title"], "دانشگاه")

    def test_30_foreign_deleted_and_unknown_tasks_have_same_preview_error(self):
        _, own = self.new_task()
        _, deleted = self.new_task()
        self.assertEqual(self.a.delete(f"/api/v1/tasks/{deleted['id']}", params={"expected_version": 1}).status_code, 204)
        foreign = self.b.post("/api/v1/tasks", json={"client_request_id": str(uuid4()), "title": "private", "duration_minutes": 30}).json()
        failures = []
        for inaccessible in [foreign, deleted, {"id": str(uuid4())}]:
            response = self.a.post("/api/v1/schedules/preview", json=self.preview_request(own, inaccessible))
            self.assertEqual(response.status_code, 404, response.text)
            failures.append(response.json())
            self.assertNotIn("private", response.text)
        self.assertEqual(failures[0], failures[1])
        self.assertEqual(failures[0], failures[2])

    def test_31_preview_requires_session_csrf_and_valid_owner_contract(self):
        data = self.preview_request()
        with TestClient(self.app, base_url=ORIGIN) as client:
            self.assertEqual(client.post("/api/v1/schedules/preview", json=data).status_code, 401)
        self.assertEqual(self.a.post("/api/v1/schedules/preview", json=data, headers={"X-CSRF-Token": "wrong"}).status_code, 403)
        self.assertEqual(self.a.post("/api/v1/schedules/preview", json=data, headers={"Origin": "https://other.example"}).status_code, 403)
        for injected in [{"user_id": self.owner_b}, {"timezone": "UTC"}]:
            response = self.a.post("/api/v1/schedules/preview", json={**data, **injected})
            self.assertEqual(response.status_code, 422, response.text)

    def test_32_conflicting_locks_are_explicit_and_do_not_leak_capacity(self):
        _, task = self.new_task()
        data = self.preview_request(task)
        start = data["availability"][0]["start"]
        end = (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()
        data["previous_blocks"] = [{"task_id": task["id"], "start": start, "end": end, "locked": True}]
        data["fixed_events"] = [{"title": "class", "start": start, "end": end}]
        for _ in range(3):
            response = self.a.post("/api/v1/schedules/preview", json=data)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["code"], "locked_block_conflict")
        data["fixed_events"] = []
        valid = self.a.post("/api/v1/schedules/preview", json=data)
        self.assertEqual(valid.status_code, 200, valid.text)
        self.assertTrue(valid.json()["blocks"][0]["locked"])
        self.assertEqual(self.a.get(f"/api/v1/tasks/{task['id']}").json()["version"], 1)

    def test_33_preview_reads_current_versions_and_omits_completed_tasks(self):
        _, task = self.new_task()
        path = f"/api/v1/tasks/{task['id']}"
        self.assertEqual(self.a.patch(path, json={"expected_version": 1, "duration_minutes": 90}).status_code, 200)
        preferences = self.a.get("/api/v1/me/preferences").json()
        changed = self.a.put("/api/v1/me/preferences", json={"expected_version": preferences["version"], "values": {"workload": "intense", "break_minutes": 0}})
        self.assertEqual(changed.status_code, 200, changed.text)
        response = self.a.post("/api/v1/schedules/preview", json=self.preview_request(task))
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["task_versions"], {task["id"]: 2})
        self.assertEqual(result["preference_version"], changed.json()["version"])
        placed = result["blocks"][0]
        self.assertEqual(datetime.fromisoformat(placed["end"]) - datetime.fromisoformat(placed["start"]), timedelta(minutes=90))
        self.assertEqual(self.a.patch(path, json={"expected_version": 2, "status": "completed"}).status_code, 200)
        result = self.a.post("/api/v1/schedules/preview", json=self.preview_request(task, previous_blocks=[{**placed, "locked": True}])).json()
        self.assertEqual(result["blocks"], [])
        self.assertEqual(result["ignored_task_ids"], [task["id"]])
        self.assertEqual(result["task_versions"], {task["id"]: 3})

    def test_34_unplaced_work_is_reported_with_duration_and_reason(self):
        _, task = self.new_task(duration_minutes=90)
        response = self.a.post("/api/v1/schedules/preview", json=self.preview_request(task, availability=[]))
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["blocks"], [])
        missing = result["unscheduled"][0]
        self.assertEqual((missing["task_id"], missing["remaining_minutes"], missing["reason"]), (task["id"], 90, "no_slot_found"))
        self.assertTrue(missing["message"])

    @unittest.skipUnless(FLAVOR == "native", "Snapshot visibility across connections requires native PostgreSQL.")
    def test_35_task_and_preferences_come_from_one_snapshot_during_edit(self):
        _, task = self.new_task()
        preferences = self.a.get("/api/v1/me/preferences").json()
        original = Repository.get_preferences
        def edit_after_snapshot(repo, owner):
            before = original(repo, owner)
            # Commit a paired edit between the two reads in planning_inputs.
            with connect(DB_URL) as writer:
                with writer.transaction():
                    writer.execute("UPDATE tasks SET duration_minutes=90, version=version+1 WHERE id=%s", (UUID(task["id"]),))
                    writer.execute("UPDATE user_preferences SET focus_block_minutes=60, version=version+1 WHERE user_id=%s", (owner,))
            return before
        with patch.object(Repository, "get_preferences", edit_after_snapshot):
            response = self.a.post("/api/v1/schedules/preview", json=self.preview_request(task))
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["task_versions"], {task["id"]: 1})
        self.assertEqual(result["preference_version"], preferences["version"])
        self.assertEqual(self.a.get(f"/api/v1/tasks/{task['id']}").json()["version"], 2)
        self.assertEqual(self.a.get("/api/v1/me/preferences").json()["version"], preferences["version"] + 1)

    @unittest.skipUnless(FLAVOR == "native", "Concurrent API connections require native PostgreSQL.")
    def test_36_busy_planner_rejects_extra_work_and_recovers(self):
        entered, release = Barrier(3), Event()
        data = self.preview_request()
        def held_preview(*args, **kwargs):
            entered.wait(timeout=10)
            if not release.wait(timeout=10):
                raise TimeoutError("Test did not release held previews.")
            return build_preview(*args, **kwargs)
        # Separate clients share the application and the same authenticated session.
        with TestClient(self.app, base_url=ORIGIN, cookies=self.a.cookies, headers=dict(self.a.headers)) as one, \
             TestClient(self.app, base_url=ORIGIN, cookies=self.a.cookies, headers=dict(self.a.headers)) as two:
            with patch("smartplanner.api.build_preview", held_preview), ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(client.post, "/api/v1/schedules/preview", json=data) for client in (one, two)]
                try:
                    entered.wait(timeout=10)
                    rejected = self.a.post("/api/v1/schedules/preview", json=data)
                    self.assertEqual(rejected.status_code, 429, rejected.text)
                    self.assertEqual(rejected.json()["code"], "planner_busy")
                    self.assertIn("retry-after", rejected.headers)
                finally:
                    release.set()
                self.assertEqual([future.result(timeout=10).status_code for future in futures], [200, 200])
        self.assertEqual(self.a.post("/api/v1/schedules/preview", json=data).status_code, 200)


if __name__ == "__main__":
    unittest.main()
