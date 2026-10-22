"""SQLite acceptance path for the low-cost single-server deployment."""

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from smartplanner.api import create_app
from smartplanner.migrate import migrate
from smartplanner.settings import Settings
from scripts.sqlite_backup import backup, restore, verify


class SQLiteApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.url = "sqlite:///" + (Path(self.temp.name) / "planner.db").as_posix()
        self.assertEqual(migrate(self.url, Path("db/migrations")), [6])
        self.assertEqual(migrate(self.url, Path("db/migrations")), [])
        self.app = create_app(Settings(self.url, auth_requests_per_minute=1000))
        self.a = TestClient(self.app, base_url="http://localhost:8000")
        self.b = TestClient(self.app, base_url="http://localhost:8000")
        for client, email in ((self.a, "a@example.com"), (self.b, "b@example.com")):
            self.assertEqual(client.post("/api/v1/auth/register", json={"email": email, "password": "test-password-123", "timezone": "UTC"}).status_code, 201)
            response = client.post("/api/v1/auth/login", json={"email": email, "password": "test-password-123"})
            self.assertEqual(response.status_code, 200, response.text)
            client.headers["X-CSRF-Token"] = response.json()["csrf_token"]

    def tearDown(self):
        self.a.close()
        self.b.close()
        self.temp.cleanup()

    def test_account_task_recurrence_schedule_and_restart(self):
        day = (datetime.now(timezone.utc).date() + timedelta(days=2)).isoformat()
        at = lambda hour: f"{day}T{hour:02d}:00:00+00:00"
        self.assertEqual(self.a.get("/api/v1/ready").status_code, 200)
        task = self.a.post("/api/v1/tasks", json={"client_request_id": str(uuid4()), "title": "Study", "duration_minutes": 60})
        self.assertEqual(task.status_code, 201, task.text)
        task = task.json()
        self.assertEqual(self.b.get(f"/api/v1/tasks/{task['id']}").status_code, 404)
        event = self.a.post("/api/v1/fixed-events", json={"title": "Class", "start": at(10), "end": at(11)})
        self.assertEqual(event.status_code, 201, event.text)
        preference = self.a.get("/api/v1/me/preferences").json()
        update = self.a.put("/api/v1/me/preferences", json={"expected_version": preference["version"], "values": {"activity_start": "08:00", "activity_end": "17:00"}})
        self.assertEqual(update.status_code, 200, update.text)
        series = self.a.post("/api/v1/recurring-activities", json={"client_request_id": str(uuid4()), "title": "Exercise", "duration_minutes": 30, "frequency": "daily", "start_date": day})
        self.assertEqual(series.status_code, 201, series.text)
        series = series.json()
        generated = self.a.post(f"/api/v1/recurring-activities/{series['id']}/occurrences", json={"expected_version": 1, "start_date": day, "days": 1})
        self.assertEqual(generated.status_code, 200, generated.text)
        self.assertEqual(generated.json()["created_count"], 1)
        draft = {"client_request_id": str(uuid4()), "title": "Day", "content": {"start_date": day, "days": 1, "task_ids": [task["id"]], "availability": [{"start": at(9), "end": at(18)}], "fixed_events": [{"title": "Class", "start": at(10), "end": at(11)}], "blocks": [{"task_id": task["id"], "start": at(9), "end": at(10), "locked": False}]}, "task_versions": {task["id"]: task["version"]}, "preference_version": update.json()["version"]}
        saved = self.a.post("/api/v1/schedules", json=draft)
        self.assertEqual(saved.status_code, 201, saved.text)
        schedule_id = saved.json()["schedule"]["id"]
        self.assertEqual(self.b.get(f"/api/v1/schedules/{schedule_id}").status_code, 404)
        self.assertEqual(len(self.a.get("/api/v1/schedules").json()), 1)
        changed = {**draft, "client_request_id": str(uuid4()), "expected_version": 1}
        changed["content"] = {**draft["content"], "blocks": [{"task_id": task["id"], "start": at(11), "end": at(12), "locked": False}]}
        moved = self.a.put(f"/api/v1/schedules/{schedule_id}", json=changed)
        self.assertEqual(moved.status_code, 200, moved.text)
        undone = self.a.post(f"/api/v1/schedules/{schedule_id}/undo", json={"client_request_id": str(uuid4()), "expected_version": 2})
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual(undone.json()["schedule"]["state"]["content"]["blocks"][0]["start"], at(9).replace("+00:00", "Z"))
        self.assertEqual(len(self.a.get(f"/api/v1/schedules/{schedule_id}/history").json()), 2)
        with TestClient(create_app(Settings(self.url)), base_url="http://localhost:8000") as reopened:
            login = reopened.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "test-password-123"})
            self.assertEqual(login.status_code, 200, login.text)
            self.assertEqual(reopened.get(f"/api/v1/schedules/{schedule_id}").status_code, 200)
            self.assertEqual(reopened.get(f"/api/v1/tasks/{task['id']}").status_code, 200)

    def test_task_and_recurrence_edits_are_versioned(self):
        day = (datetime.now(timezone.utc).date() + timedelta(days=2)).isoformat()
        created = self.a.post("/api/v1/tasks", json={"client_request_id": str(uuid4()), "title": "Read", "duration_minutes": 30})
        self.assertEqual(created.status_code, 201, created.text)
        task = created.json()
        edited = self.a.patch(f"/api/v1/tasks/{task['id']}", json={"expected_version": 1, "title": "Read more"})
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(self.a.patch(f"/api/v1/tasks/{task['id']}", json={"expected_version": 1, "title": "Stale"}).status_code, 409)
        series = self.a.post("/api/v1/recurring-activities", json={"client_request_id": str(uuid4()), "title": "Walk", "duration_minutes": 30, "frequency": "daily", "start_date": day})
        self.assertEqual(series.status_code, 201, series.text)
        record = series.json()
        values = {key: value for key, value in record.items() if key not in {"id", "user_id", "client_request_id", "timezone", "version", "created_at", "updated_at"}}
        values["active"] = False
        paused = self.a.put(f"/api/v1/recurring-activities/{record['id']}", json={"expected_version": 1, "values": values})
        self.assertEqual(paused.status_code, 200, paused.text)
        self.assertFalse(paused.json()["active"])

    def test_online_backup_and_restore_to_empty_database(self):
        created = self.a.post("/api/v1/tasks", json={"client_request_id": str(uuid4()), "title": "Keep me", "duration_minutes": 30})
        self.assertEqual(created.status_code, 201, created.text)
        source = Path(self.temp.name) / "planner.db"
        snapshot = Path(self.temp.name) / "backup.db"
        restored = Path(self.temp.name) / "restored.db"
        backup(source, snapshot)
        self.assertEqual(verify(snapshot), 6)
        restore(snapshot, restored)
        with TestClient(create_app(Settings("sqlite:///" + restored.as_posix())), base_url="http://localhost:8000") as client:
            login = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "test-password-123"})
            self.assertEqual(login.status_code, 200, login.text)
            self.assertEqual(client.get(f"/api/v1/tasks/{created.json()['id']}").status_code, 200)

    def test_concurrent_edits_preserve_one_winner(self):
        created = self.a.post("/api/v1/tasks", json={"client_request_id": str(uuid4()), "title": "Race", "duration_minutes": 30})
        self.assertEqual(created.status_code, 201, created.text)
        task_id = created.json()["id"]
        clients = [TestClient(self.app, base_url="http://localhost:8000") for _ in range(2)]
        try:
            for client in clients:
                login = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "test-password-123"})
                self.assertEqual(login.status_code, 200, login.text)
                client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda item: item[0].patch(f"/api/v1/tasks/{task_id}", json={"expected_version": 1, "title": item[1]}).status_code,
                                        zip(clients, ("Winner A", "Winner B"))))
            self.assertEqual(sorted(results), [200, 409])
            self.assertEqual(self.a.get(f"/api/v1/tasks/{task_id}").json()["version"], 2)
        finally:
            for client in clients:
                client.close()

    def test_small_four_worker_read_burst(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda index: (self.a if index % 2 else self.b).get("/api/v1/tasks").status_code,
                                      range(20)))
        self.assertEqual(responses, [200] * 20)


if __name__ == "__main__":
    unittest.main()
