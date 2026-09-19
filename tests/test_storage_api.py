"""Integration tests require an explicitly selected, disposable PostgreSQL test database."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from uuid import UUID, uuid4

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


if __name__ == "__main__":
    unittest.main()
