"""Generate local SQLite configuration without overwriting existing settings."""
from pathlib import Path

target = Path('.env')
try:
    with target.open('x', encoding='utf-8') as file:
        file.write('DATABASE_URL=sqlite:///data/smartplanner.db\n')
        file.write('APP_ORIGIN=http://localhost:8000\nAPP_ENV=development\nSESSION_COOKIE_SECURE=false\n')
    target.chmod(0o600)
    print('Local configuration created. Existing data was not changed.')
except FileExistsError:
    print('Existing .env preserved.')
