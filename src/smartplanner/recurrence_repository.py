"""Owner-scoped recurrence writes and atomic, retry-safe task materialization."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from .errors import AppError, not_found, stale_version
from .models import TaskRecord
from .recurrence import occurrence_dates, occurrence_window
from .recurrence_models import OccurrenceResult, RecurrenceRecord
from .repository import TASK_COLUMNS

RECURRENCE_COLUMNS = (
    "id, user_id, client_request_id, title, description, duration_minutes, priority, "
    "splittable, preferred_period, frequency, weekdays, start_date, end_date, active, "
    "timezone, version, created_at, updated_at"
)


class RecurrenceRepository:
    def __init__(self, connection):
        self.conn = connection

    def _get(self, owner, recurrence_id, *, lock=False):
        row = self.conn.execute(
            f"SELECT {RECURRENCE_COLUMNS} FROM recurring_activities "
            "WHERE id=%s AND user_id=%s AND deleted_at IS NULL" + (" FOR UPDATE" if lock else ""),
            (recurrence_id, owner),
        ).fetchone()
        if row is None:
            raise not_found()
        return RecurrenceRecord.model_validate(row)

    def get(self, owner, recurrence_id):
        return self._get(owner, recurrence_id)

    def list(self, owner, limit=50, offset=0):
        rows = self.conn.execute(
            f"SELECT {RECURRENCE_COLUMNS} FROM recurring_activities "
            "WHERE user_id=%s AND deleted_at IS NULL ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
            (owner, limit, offset),
        ).fetchall()
        return [RecurrenceRecord.model_validate(row) for row in rows]

    def create(self, owner, data):
        digest = hashlib.sha256(json.dumps(data.model_dump(mode="json"), sort_keys=True,
                                          separators=(",", ":")).encode()).hexdigest()
        params = data.model_dump()
        params.update(user_id=owner, creation_digest=digest, weekdays=list(data.weekdays))
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            row = self.conn.execute(
                "INSERT INTO recurring_activities(user_id, client_request_id, creation_digest, title, description, "
                "duration_minutes, priority, splittable, preferred_period, frequency, weekdays, start_date, end_date, active, timezone) "
                "SELECT %(user_id)s, %(client_request_id)s, %(creation_digest)s, %(title)s, %(description)s, "
                "%(duration_minutes)s, %(priority)s, %(splittable)s, %(preferred_period)s, %(frequency)s, "
                "%(weekdays)s::text[], %(start_date)s, %(end_date)s, %(active)s, timezone FROM users WHERE id=%(user_id)s "
                f"ON CONFLICT (user_id, client_request_id) DO NOTHING RETURNING {RECURRENCE_COLUMNS}", params,
            ).fetchone()
            replayed = row is None
            if replayed:
                row = self.conn.execute(
                    f"SELECT {RECURRENCE_COLUMNS}, creation_digest, deleted_at FROM recurring_activities "
                    "WHERE user_id=%s AND client_request_id=%s", (owner, data.client_request_id),
                ).fetchone()
                if row is None or row["creation_digest"] != digest:
                    raise AppError(409, "request_id_conflict", "شناسهٔ درخواست قبلاً برای دادهٔ دیگری استفاده شده است.")
                if row["deleted_at"] is not None:
                    raise AppError(409, "request_already_deleted", "الگوی این درخواست قبلاً حذف شده است.")
                row = {key: value for key, value in row.items() if key not in {"creation_digest", "deleted_at"}}
            result = RecurrenceRecord.model_validate(row)
        return result, replayed

    @staticmethod
    def _check_version(record, expected, *, increment=False):
        if record.version != expected or (increment and record.version == 2_147_483_647):
            raise stale_version()

    def replace(self, owner, recurrence_id, data):
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            current = self._get(owner, recurrence_id, lock=True)
            self._check_version(current, data.expected_version, increment=True)
            params = data.values.model_dump()
            params.update(id=recurrence_id, owner=owner, weekdays=list(data.values.weekdays))
            row = self.conn.execute(
                "UPDATE recurring_activities SET title=%(title)s, description=%(description)s, "
                "duration_minutes=%(duration_minutes)s, priority=%(priority)s, splittable=%(splittable)s, "
                "preferred_period=%(preferred_period)s, frequency=%(frequency)s, weekdays=%(weekdays)s::text[], "
                "start_date=%(start_date)s, end_date=%(end_date)s, active=%(active)s, "
                "version=version+1, updated_at=GREATEST(clock_timestamp(), updated_at) "
                f"WHERE id=%(id)s AND user_id=%(owner)s RETURNING {RECURRENCE_COLUMNS}", params,
            ).fetchone()
            result = RecurrenceRecord.model_validate(row)
        return result

    def delete(self, owner, recurrence_id, expected_version):
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            current = self._get(owner, recurrence_id, lock=True)
            self._check_version(current, expected_version, increment=True)
            self.conn.execute(
                "UPDATE recurring_activities SET deleted_at=clock_timestamp(), version=version+1, "
                "updated_at=GREATEST(clock_timestamp(), updated_at) WHERE id=%s AND user_id=%s",
                (recurrence_id, owner),
            )

    def _insert_occurrences(self, owner, series, days):
        if not days:
            return []
        params = []
        for day in days:
            start, end = occurrence_window(day, series.timezone, series.duration_minutes)
            params.extend((owner, uuid4(), series.title, series.description, series.duration_minutes,
                           series.priority, series.splittable, series.preferred_period,
                           start, end, series.id, day))
        # At most seven rows; only placeholder count is interpolated, never values.
        placeholders = ",".join(["(" + ",".join(["%s"] * 12) + ")"] * len(days))
        rows = self.conn.execute(
            "INSERT INTO tasks(user_id, client_request_id, title, description, duration_minutes, priority, "
            "splittable, preferred_period, earliest_start, deadline, recurrence_id, occurrence_date) "
            f"VALUES {placeholders} RETURNING {TASK_COLUMNS}", params,
        ).fetchall()
        return [TaskRecord.model_validate(row) for row in rows]

    def materialize(self, owner, recurrence_id, request, *, now=None):
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            series = self._get(owner, recurrence_id, lock=True)
            self._check_version(series, request.expected_version)
            # Include already generated dates even after the pattern changes. Lock in
            # the same ID order as schedule writes; edits cannot produce a torn response.
            rows = self.conn.execute(
                f"SELECT {TASK_COLUMNS}, deleted_at FROM tasks WHERE user_id=%s AND recurrence_id=%s "
                "AND occurrence_date >= %s AND occurrence_date < %s ORDER BY id FOR SHARE",
                (owner, recurrence_id, request.start_date, request.start_date + timedelta(days=request.days)),
            ).fetchall()
            # Capture the local date after lock waits (which may cross midnight).
            today = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(series.timezone)).date()
            existing = {row["occurrence_date"] for row in rows}
            tasks = [TaskRecord.model_validate({key: value for key, value in row.items() if key != "deleted_at"})
                     for row in rows if row["deleted_at"] is None]
            deleted = tuple(sorted(row["occurrence_date"] for row in rows if row["deleted_at"] is not None))
            skipped = []
            new_dates = []
            for day in occurrence_dates(series, request.start_date, request.days):
                if day in existing:
                    continue
                if day < today:
                    skipped.append(day)
                    continue
                new_dates.append(day)
            tasks.extend(self._insert_occurrences(owner, series, new_dates))
            result = OccurrenceResult(recurrence_id=series.id, recurrence_version=series.version,
                                      tasks=tuple(sorted(tasks, key=lambda task: task.occurrence_date)),
                                      created_count=len(new_dates), deleted_dates=deleted, skipped_past_dates=tuple(skipped))
        return result
