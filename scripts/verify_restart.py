"""Run seed, stop/restart the database, then verify with the same state file.

Uses fictional test accounts only. Requires an explicitly selected test database.
"""

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from smartplanner.api import create_app
from smartplanner.migrate import migrate
from smartplanner.settings import Settings

PASSWORD = "Restart probe only 2026!"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["seed", "verify"])
    parser.add_argument("state_file", type=Path)
    args = parser.parse_args()
    url = os.environ.get("SMARTPLANNER_TEST_DATABASE_URL")
    if not url:
        raise SystemExit("Select a disposable database with SMARTPLANNER_TEST_DATABASE_URL.")
    migrate(url, Path("db/migrations"))
    with TestClient(create_app(Settings(url)), base_url="http://localhost:8000") as client:
        if args.mode == "seed":
            state = {"email": f"restart-{uuid4().hex}@example.com"}
            response = client.post("/api/v1/auth/register", json={"email": state["email"], "password": PASSWORD})
            assert response.status_code == 201, response.text
        else:
            state = json.loads(args.state_file.read_text())
        response = client.post("/api/v1/auth/login", json={"email": state["email"], "password": PASSWORD})
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        if args.mode == "seed":
            response = client.post("/api/v1/tasks", json={"client_request_id": str(uuid4()), "title": "انگلیسی پس از شروع دوباره", "duration_minutes": 60})
            assert response.status_code == 201, response.text
            state["task_id"] = response.json()["id"]
            day = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
            content = {"start_date": day, "days": 1, "task_ids": [state["task_id"]],
                       "availability": [{"start": f"{day}T09:00:00Z", "end": f"{day}T18:00:00Z"}],
                       "blocks": [{"task_id": state["task_id"], "start": f"{day}T09:00:00Z", "end": f"{day}T10:00:00Z"}]}
            create = {"client_request_id": str(uuid4()), "title": "برنامه پس از شروع دوباره", "content": content,
                      "task_versions": {state["task_id"]: 1}, "preference_version": 1}
            response = client.post("/api/v1/schedules", json=create)
            assert response.status_code == 201, response.text
            state["schedule_id"] = response.json()["schedule"]["id"]
            path = f"/api/v1/schedules/{state['schedule_id']}"
            content["blocks"][0].update(start=f"{day}T11:00:00Z", end=f"{day}T12:00:00Z")
            response = client.put(path, json={**create, "client_request_id": str(uuid4()), "expected_version": 1})
            assert response.status_code == 200, response.text
            state["moved_state"] = response.json()["schedule"]["state"]
            state["undo_request"] = {"client_request_id": str(uuid4()), "expected_version": 2}
            response = client.post(path + "/undo", json=state["undo_request"])
            assert response.status_code == 200, response.text
            state["saved_schedule"] = response.json()["schedule"]
            assert state["saved_schedule"]["can_redo"]
            response = client.post("/api/v1/recurring-activities", json={
                "client_request_id": str(uuid4()), "title": "تکرار پس از شروع دوباره",
                "duration_minutes": 30, "frequency": "daily", "start_date": day})
            assert response.status_code == 201, response.text
            state["recurrence"] = response.json()
            recurrence_path = f"/api/v1/recurring-activities/{state['recurrence']['id']}"
            state["occurrence_request"] = {"expected_version": 1, "start_date": day, "days": 7}
            response = client.post(recurrence_path + "/occurrences", json=state["occurrence_request"])
            assert response.status_code == 200, response.text
            occurrences = response.json()
            assert occurrences["created_count"] == 7
            response = client.patch(f"/api/v1/tasks/{occurrences['tasks'][0]['id']}", json={"expected_version": 1, "status": "completed"})
            assert response.status_code == 200, response.text
            response = client.delete(f"/api/v1/tasks/{occurrences['tasks'][1]['id']}", params={"expected_version": 1})
            assert response.status_code == 204, response.text
            response = client.post(recurrence_path + "/occurrences", json=state["occurrence_request"])
            assert response.status_code == 200, response.text
            state["occurrences"] = response.json()
            assert state["occurrences"]["created_count"] == 0
            args.state_file.write_text(json.dumps(state), encoding="utf-8")
            print("Restart probe seeded; now restart the database.")
        else:
            response = client.get(f"/api/v1/tasks/{state['task_id']}")
            assert response.status_code == 200, response.text
            assert response.json()["title"] == "انگلیسی پس از شروع دوباره"
            assert response.json()["duration_minutes"] == 60
            path = f"/api/v1/schedules/{state['schedule_id']}"
            response = client.get(path)
            assert response.status_code == 200, response.text
            assert response.json() == state["saved_schedule"]
            replay = client.post(path + "/undo", json=state["undo_request"])
            assert replay.status_code == 200, replay.text
            assert replay.json()["replayed"] and replay.json()["applied_version"] == 3
            assert replay.json()["schedule"] == state["saved_schedule"]
            redo = {"client_request_id": str(uuid4()), "expected_version": 3}
            response = client.post(path + "/redo", json=redo)
            assert response.status_code == 200, response.text
            assert response.json()["schedule"]["version"] == 4
            assert response.json()["schedule"]["state"] == state["moved_state"]
            assert client.post(path + "/redo", json=redo).json()["replayed"]
            assert len(client.get(path + "/history").json()) == 2
            recurrence_path = f"/api/v1/recurring-activities/{state['recurrence']['id']}"
            response = client.get(recurrence_path)
            assert response.status_code == 200, response.text
            assert response.json() == state["recurrence"]
            response = client.post(recurrence_path + "/occurrences", json=state["occurrence_request"])
            assert response.status_code == 200, response.text
            assert response.json() == state["occurrences"]
            assert len(response.json()["tasks"]) == 6
            assert response.json()["tasks"][0]["status"] == "completed"
            assert len(response.json()["deleted_dates"]) == 1
            print("PASS: account, task, saved schedule, undo/redo history, request receipts and recurrence instances/tombstones survived database/process restart.")


if __name__ == "__main__":
    main()
