"""Online SQLite backup, verification and non-overwriting restore."""

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

from smartplanner.settings import Settings


def database_path():
    url = Settings.from_environment().database_url
    if not url.startswith("sqlite:///"):
        raise SystemExit("This command requires a SQLite DATABASE_URL.")
    return Path(url.removeprefix("sqlite:///"))


def verify(path):
    if not path.is_file():
        raise SystemExit(f"Database file not found: {path}")
    with closing(sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)) as source:
        result = source.execute("PRAGMA quick_check").fetchone()[0]
        version = source.execute("PRAGMA user_version").fetchone()[0]
    if result != "ok" or version != 6:
        raise SystemExit(f"Invalid backup: integrity={result}, schema={version}.")
    return version


def backup(source, target):
    verify(source)
    if target.exists() or target.is_symlink():
        raise SystemExit("Backup destination already exists; refusing to overwrite it.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb"):
        pass
    target.chmod(0o600)
    try:
        with closing(sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True)) as live:
            with closing(sqlite3.connect(target)) as copy:
                live.backup(copy)
        verify(target)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def restore(source, target):
    verify(source)
    if target.exists() or target.is_symlink() or Path(str(target) + "-wal").exists() or Path(str(target) + "-shm").exists():
        raise SystemExit("Restore target already exists; stop the app and restore into an empty data directory.")
    backup(source, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "verify", "restore"])
    parser.add_argument("path", type=Path, help="backup file path")
    args = parser.parse_args()
    database = database_path()
    if args.action == "backup":
        backup(database, args.path)
    elif args.action == "verify":
        verify(args.path)
    else:
        restore(args.path, database)
    print(f"{args.action}: OK")


if __name__ == "__main__":
    main()
