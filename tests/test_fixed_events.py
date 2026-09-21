"""Acceptance tests for durable fixed commitments and planner integration."""

import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

DB_URL = os.getenv("SMARTPLANNER_TEST_DATABASE_URL")
ORIGIN = "http://localhost:8000"
PASSWORD = "Fixed event tests only 2026!"

if DB_URL:
    from fastapi.testclient import TestClient
    from smartplanner.api import create_app
    from smartplanner.migrate import migrate
    from smartplanner.settings import Settings


@unittest.skipUnless(DB_URL, "Select a disposable PostgreSQL database.")
class FixedEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate(DB_URL, Path("db/migrations"))
        cls.app = create_app(Settings(DB_URL, auth_requests_per_minute=1000))
        cls.a = TestClient(cls.app, base_url=ORIGIN)
        cls.b = TestClient(cls.app, base_url=ORIGIN)
        for client in (cls.a, cls.b):
            email = f"fixed-{uuid4().hex}@example.com"
            assert client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD}).status_code == 201
            login = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
            assert login.status_code == 200, login.text
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]

    @classmethod
    def tearDownClass(cls):
        cls.a.close(); cls.b.close()

    def setUp(self):
        self.day = datetime.now(timezone.utc).date() + timedelta(days=30)
        self.start = datetime.combine(self.day, datetime.min.time(), timezone.utc) + timedelta(hours=10)
        self.end = self.start + timedelta(hours=1)

    def create_event(self):
        response = self.a.post("/api/v1/fixed-events", json={"title": "University", "start": self.start.isoformat(), "end": self.end.isoformat()})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_crud_is_user_isolated_and_versioned(self):
        event = self.create_event()
        self.assertEqual(self.a.get(f"/api/v1/fixed-events/{event['id']}").status_code, 200)
        self.assertEqual(self.b.get(f"/api/v1/fixed-events/{event['id']}").status_code, 404)
        changed = self.a.put(f"/api/v1/fixed-events/{event['id']}", json={
            "title": "Class", "start": self.start.isoformat(), "end": self.end.isoformat(), "expected_version": event["version"]})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["version"], event["version"] + 1)
        stale = self.a.delete(f"/api/v1/fixed-events/{event['id']}?expected_version={event['version']}")
        self.assertEqual(stale.status_code, 409)
        current = changed.json()
        self.assertEqual(self.a.delete(f"/api/v1/fixed-events/{event['id']}?expected_version={current['version']}").status_code, 204)

    def test_stored_event_is_automatically_a_hard_planner_constraint(self):
        event = self.create_event()
        task = self.a.post("/api/v1/tasks", json={
            "client_request_id": str(uuid4()), "title": "Study", "duration_minutes": 60,
            "earliest_start": (self.start - timedelta(hours=1)).isoformat(), "deadline": (self.end + timedelta(hours=1)).isoformat()})
        self.assertEqual(task.status_code, 201, task.text)
        preview = self.a.post("/api/v1/schedules/preview", json={
            "start_date": self.day.isoformat(), "days": 1, "task_ids": [task.json()["id"]],
            "availability": [{"start": (self.start - timedelta(hours=1)).isoformat(), "end": (self.end + timedelta(hours=1)).isoformat()}]})
        self.assertEqual(preview.status_code, 200, preview.text)
        body = preview.json()
        self.assertIn(event["title"], [item["title"] for item in body["fixed_events"]])
        for block in body["blocks"]:
            block_start = datetime.fromisoformat(block["start"])
            block_end = datetime.fromisoformat(block["end"])
            self.assertTrue(block_end <= self.start or block_start >= self.end)


if __name__ == "__main__":
    unittest.main()
