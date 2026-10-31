"""Run the real API against a disposable SQLite database for browser tests."""

import os
import tempfile
from pathlib import Path

import uvicorn

from smartplanner.api import create_app
from smartplanner.migrate import migrate
from smartplanner.settings import Settings


with tempfile.TemporaryDirectory(prefix="smartplanner-e2e-") as directory:
    os.environ["DATABASE_URL"] = "sqlite:///" + (Path(directory) / "planner.db").as_posix()
    os.environ["APP_ORIGIN"] = "http://localhost:8765"
    os.environ["APP_ENV"] = "development"
    os.environ["SESSION_COOKIE_SECURE"] = "false"
    migrate(os.environ["DATABASE_URL"], Path("db/migrations"))
    uvicorn.run(create_app(Settings(os.environ["DATABASE_URL"],
                           origin=os.environ["APP_ORIGIN"], auth_requests_per_minute=1000)),
                host="127.0.0.1", port=8765, access_log=False)
