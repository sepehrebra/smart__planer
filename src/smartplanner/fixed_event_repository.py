"""PostgreSQL persistence for user-owned fixed commitments."""

from uuid import uuid4

from .errors import AppError
from .fixed_event_models import FixedEventCreate, FixedEventRecord, FixedEventReplace


class FixedEventRepository:
    def __init__(self, conn):
        self.conn = conn

    @staticmethod
    def _record(row):
        return FixedEventRecord.model_validate({
            "id": row["id"], "title": row["title"], "start": row["starts_at"], "end": row["ends_at"],
            "version": row["version"], "created_at": row["created_at"], "updated_at": row["updated_at"],
        })

    def create(self, user_id, data: FixedEventCreate):
        row = self.conn.execute(
            """INSERT INTO fixed_events(id,user_id,title,starts_at,ends_at)
               VALUES (%s,%s,%s,%s,%s)
               RETURNING *""",
            (uuid4(), user_id, data.title, data.start, data.end),
        ).fetchone()
        return self._record(row)

    def list(self, user_id, start=None, end=None, limit=100, offset=0):
        rows = self.conn.execute(
            """SELECT * FROM fixed_events
               WHERE user_id=%s
                 AND (%s::timestamptz IS NULL OR ends_at > %s)
                 AND (%s::timestamptz IS NULL OR starts_at < %s)
               ORDER BY starts_at,id LIMIT %s OFFSET %s""",
            (user_id, start, start, end, end, limit, offset),
        ).fetchall()
        return [self._record(row) for row in rows]

    def get(self, user_id, event_id):
        row = self.conn.execute("SELECT * FROM fixed_events WHERE user_id=%s AND id=%s", (user_id, event_id)).fetchone()
        if row is None:
            raise AppError(404, "fixed_event_not_found", "تعهد ثابت پیدا نشد.")
        return self._record(row)

    def replace(self, user_id, event_id, data: FixedEventReplace):
        row = self.conn.execute(
            """UPDATE fixed_events SET title=%s,starts_at=%s,ends_at=%s,version=version+1,updated_at=now()
               WHERE user_id=%s AND id=%s AND version=%s RETURNING *""",
            (data.title, data.start, data.end, user_id, event_id, data.expected_version),
        ).fetchone()
        if row is None:
            self.get(user_id, event_id)
            raise AppError(409, "stale_fixed_event", "این تعهد در جای دیگری تغییر کرده؛ صفحه را تازه کنید.")
        return self._record(row)

    def delete(self, user_id, event_id, expected_version):
        row = self.conn.execute(
            "DELETE FROM fixed_events WHERE user_id=%s AND id=%s AND version=%s RETURNING id",
            (user_id, event_id, expected_version),
        ).fetchone()
        if row is None:
            self.get(user_id, event_id)
            raise AppError(409, "stale_fixed_event", "این تعهد در جای دیگری تغییر کرده؛ صفحه را تازه کنید.")

    def planning_events(self, user_id, start, end):
        return self.list(user_id, start=start, end=end, limit=100, offset=0)
