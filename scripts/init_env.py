"""Generate local configuration without overwriting existing secrets."""
from pathlib import Path
import secrets

target = Path('.env')
try:
    with target.open('x', encoding='utf-8') as file:
        file.write('POSTGRES_PASSWORD=' + secrets.token_urlsafe(32) + '\n')
        file.write('APP_ORIGIN=http://localhost:8000\nAPP_ENV=development\nSESSION_COOKIE_SECURE=false\n')
    target.chmod(0o600)
    print('Local configuration created. Existing data was not changed.')
except FileExistsError:
    print('Existing .env preserved.')
