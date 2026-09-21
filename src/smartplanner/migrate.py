"""Apply the numbered SQL files once; the application never auto-migrates on import."""

import argparse
from pathlib import Path

from .database import connect
from .settings import Settings


LATEST_SCHEMA = 5


def migrate(database_url: str, directory: Path) -> list[int]:
    files = sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))
    if not files:
        raise RuntimeError("No migration files found; run from the project directory.")
    applied = []
    with connect(database_url) as conn:
        conn.execute("SELECT pg_advisory_lock(731940217)")
        try:
            exists = conn.execute("SELECT to_regclass('public.schema_migrations') AS name").fetchone()["name"]
            existing = ({row["version"] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}
                        if exists else set())
            known = {int(file.name.split("_", 1)[0]) for file in files}
            if not existing <= known:
                raise RuntimeError("Database schema is newer than this application.")
            for file in files:
                version = int(file.name.split("_", 1)[0])
                if version in existing:
                    continue
                try:
                    conn.execute(file.read_text(encoding="utf-8"))
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
                applied.append(version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(731940217)")
    return applied


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("db/migrations"))
    args = parser.parse_args()
    versions = migrate(Settings.from_environment().database_url, args.directory)
    print("Applied migrations:", versions or "already current")


if __name__ == "__main__":
    main()
