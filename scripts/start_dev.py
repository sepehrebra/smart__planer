"""Development entrypoint; production rollout is a later phase."""
from pathlib import Path
import os
from smartplanner.migrate import migrate
from smartplanner.settings import Settings

settings = Settings.from_environment()
if settings.environment != 'development':
    raise SystemExit('Use a reviewed production rollout; this entrypoint is for development.')
migrate(settings.database_url, Path('db/migrations'))
os.execvp('uvicorn', ['uvicorn', 'smartplanner.api:create_app', '--factory',
                     '--host', '0.0.0.0', '--port', '8000', '--no-access-log'])
