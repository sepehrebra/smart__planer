"""Readiness check for the disposable CI restart probe."""
import os
import time
import psycopg

url = os.environ['SMARTPLANNER_TEST_DATABASE_URL']
deadline = time.monotonic() + 30
while True:
    try:
        with psycopg.connect(url, connect_timeout=2) as conn:
            conn.execute('SELECT 1')
        print('Test database is ready.')
        break
    except psycopg.OperationalError:
        if time.monotonic() >= deadline:
            raise SystemExit('Test database did not become ready in time.')
        time.sleep(1)
