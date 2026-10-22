"""Small SQLite adapter for the existing owner-scoped repositories."""

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time, timezone
from pathlib import Path
from uuid import UUID, uuid4


UUID_COLUMNS = {"id", "user_id", "client_request_id", "task_id", "schedule_id", "recurrence_id"}


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def encode(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return json.dumps([str(item) for item in value])
    if hasattr(value, "obj") and value.__class__.__name__ == "Jsonb":
        return json.dumps(value.obj, ensure_ascii=False)
    return value


def decode(row):
    if row is None:
        return None
    result = dict(row)
    for key, value in result.items():
        if value is None:
            continue
        if key in UUID_COLUMNS:
            result[key] = UUID(value)
        elif key == "state" or key == "weekdays":
            result[key] = json.loads(value)
        elif key == "occurrence_date":
            result[key] = date.fromisoformat(value)
        elif key in {"splittable", "active"}:
            result[key] = bool(value)
    return result


class Result:
    def __init__(self, cursor):
        self.cursor = cursor

    def fetchone(self):
        return decode(self.cursor.fetchone()) if self.cursor else None

    def fetchall(self):
        return [decode(row) for row in self.cursor.fetchall()] if self.cursor else []


class SQLiteConnection:
    def __init__(self, path):
        self.raw = sqlite3.connect(path, timeout=5, isolation_level=None, check_same_thread=False)
        self.raw.row_factory = sqlite3.Row
        self.raw.execute("PRAGMA foreign_keys=ON")
        self.raw.execute("PRAGMA busy_timeout=5000")
        self.raw.execute("PRAGMA journal_mode=WAL")
        self.raw.create_function("now", 0, timestamp)
        self.raw.create_function("clock_timestamp", 0, timestamp)
        self.raw.create_function("GREATEST", -1, max)
        self.raw.create_function("uuid4", 0, lambda: str(uuid4()))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.raw.close()

    @contextmanager
    def transaction(self):
        self.raw.execute("BEGIN IMMEDIATE")
        try:
            yield self
        except BaseException:
            self.raw.rollback()
            raise
        else:
            self.raw.commit()

    def execute(self, sql, params=None):
        if sql.lstrip().upper().startswith("SET TRANSACTION"):
            return Result(None)
        sql = re.sub(r"\s+FOR (?:NO KEY UPDATE|UPDATE|SHARE)\b", "", sql)
        sql = sql.replace("::text[]", "").replace("::uuid[]", "").replace("::timestamptz", "")
        sql = re.sub(r"=\s*ANY\((%s)\)", r" IN (SELECT value FROM json_each(\1))", sql)
        sql = re.sub(r"%\((\w+)\)s", r":\1", sql).replace("%s", "?")
        sql = sql.replace("GREATEST(", "GREATEST(")
        if isinstance(params, dict):
            values = {key: encode(value) for key, value in params.items()}
        else:
            values = tuple(encode(value) for value in (params or ()))
        return Result(self.raw.execute(sql, values))


def connect_sqlite(database_url):
    if not database_url.startswith("sqlite:///"):
        raise ValueError("Use sqlite:/// followed by a database path.")
    path = Path(database_url.removeprefix("sqlite:///"))
    if str(path) != ":memory":
        path.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteConnection(path)
