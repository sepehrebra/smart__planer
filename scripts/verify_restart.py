"""Run seed, stop/restart the database, then verify with the same state file.

Uses fictional test accounts only. Requires an explicitly selected test database.
"""

import argparse
import json
import os
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
            args.state_file.write_text(json.dumps(state), encoding="utf-8")
            print("Restart probe seeded; now restart the database.")
        else:
            response = client.get(f"/api/v1/tasks/{state['task_id']}")
            assert response.status_code == 200, response.text
            assert response.json()["title"] == "انگلیسی پس از شروع دوباره"
            assert response.json()["duration_minutes"] == 60
            print("PASS: account login and saved task survived database/process restart.")


if __name__ == "__main__":
    main()
