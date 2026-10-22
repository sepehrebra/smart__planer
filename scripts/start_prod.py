"""Production entrypoint: require a migrated database and secure settings."""

import os

from smartplanner.database import connect
from smartplanner.migrate import LATEST_SCHEMA
from smartplanner.settings import Settings

settings = Settings.from_environment()
if settings.environment != "production":
    raise SystemExit("Set APP_ENV=production for this entrypoint.")
with connect(settings.database_url) as connection:
    row = connection.execute("SELECT max(version) AS version FROM schema_migrations").fetchone()
    if row["version"] != LATEST_SCHEMA:
        raise SystemExit("Database schema is not current; run migration before starting.")
os.execvp("uvicorn", ["uvicorn", "smartplanner.api:create_app", "--factory",
                     "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--workers", "1"])
