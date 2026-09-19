"""Parameterized SQL. Every task/preference operation takes the authenticated owner."""

import hashlib
import json
import secrets
from uuid import UUID

from pydantic import ValidationError

from .accounts import (
    PreferencePut, PreferenceRecord, Registration, UserView, token_digest,
)
from .errors import AppError, not_found, stale_version
from .models import TaskCreate, TaskPatch, TaskRecord
from .task_changes import StaleTaskVersion, prepare_task_change


TASK_COLUMNS = (
    "id, user_id, client_request_id, title, description, duration_minutes, priority, "
    "status, splittable, preferred_period, earliest_start, deadline, version, created_at, updated_at, "
    "recurrence_id, occurrence_date"
)
USER_COLUMNS = "id, email, timezone, created_at"


def creation_digest(task: TaskCreate) -> str:
    canonical = json.dumps(task.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Repository:
    def __init__(self, connection):
        self.conn = connection

    def register(self, data: Registration, password_hash: str) -> UserView:
        with self.conn.transaction():
            row = self.conn.execute(
                f"INSERT INTO users(email, password_hash, timezone) VALUES (%s, %s, %s) "
                f"ON CONFLICT (lower(email)) DO NOTHING RETURNING {USER_COLUMNS}",
                (data.email, password_hash, data.timezone),
            ).fetchone()
            if row is None:
                raise AppError(409, "email_unavailable", "این ایمیل قابل استفاده نیست.")
            self.conn.execute("INSERT INTO user_preferences(user_id) VALUES (%s)", (row["id"],))
            result = UserView.model_validate(row)
        return result

    def account_for_login(self, email: str):
        return self.conn.execute(
            f"SELECT {USER_COLUMNS}, password_hash FROM users WHERE lower(email) = %s", (email,)
        ).fetchone()

    def create_session(self, user_id: UUID, hours: int) -> str:
        token = secrets.token_urlsafe(32)
        with self.conn.transaction():
            self.conn.execute("DELETE FROM sessions WHERE user_id = %s AND expires_at <= now()", (user_id,))
            self.conn.execute(
                "INSERT INTO sessions(token_digest, user_id, expires_at) "
                "VALUES (%s, %s, now() + %s * INTERVAL '1 hour')",
                (token_digest(token), user_id, hours),
            )
        return token

    def session_user(self, token: str) -> UserView:
        row = self.conn.execute(
            "SELECT u.id, u.email, u.timezone, u.created_at FROM sessions s "
            "JOIN users u ON u.id = s.user_id "
            "WHERE s.token_digest = %s AND s.expires_at > now()", (token_digest(token),)
        ).fetchone()
        if row is None:
            raise AppError(401, "authentication_required", "دوباره وارد حساب شوید.")
        return UserView.model_validate(row)

    def logout(self, token: str):
        with self.conn.transaction():
            self.conn.execute("DELETE FROM sessions WHERE token_digest = %s", (token_digest(token),))

    def create_task(self, owner: UUID, task: TaskCreate) -> tuple[TaskRecord, bool]:
        digest = creation_digest(task)
        data = task.model_dump()
        data.update(user_id=owner, creation_digest=digest)
        with self.conn.transaction():
            row = self.conn.execute(
                "INSERT INTO tasks(user_id, client_request_id, title, description, duration_minutes, "
                "priority, splittable, preferred_period, earliest_start, deadline, creation_digest) "
                "VALUES (%(user_id)s, %(client_request_id)s, %(title)s, %(description)s, "
                "%(duration_minutes)s, %(priority)s, %(splittable)s, %(preferred_period)s, "
                "%(earliest_start)s, %(deadline)s, %(creation_digest)s) "
                f"ON CONFLICT (user_id, client_request_id) DO NOTHING RETURNING {TASK_COLUMNS}", data,
            ).fetchone()
            replayed = row is None
            if replayed:
                existing = self.conn.execute(
                    f"SELECT {TASK_COLUMNS}, creation_digest, deleted_at FROM tasks "
                    "WHERE user_id = %s AND client_request_id = %s", (owner, task.client_request_id)
                ).fetchone()
                if existing is None or existing["creation_digest"] != digest:
                    raise AppError(409, "request_id_conflict", "شناسهٔ این درخواست قبلاً برای دادهٔ دیگری استفاده شده است.")
                if existing["deleted_at"] is not None:
                    raise AppError(409, "request_already_deleted", "کار مربوط به این درخواست قبلاً حذف شده است.")
                row = {key: value for key, value in existing.items() if key not in {"creation_digest", "deleted_at"}}
            result = TaskRecord.model_validate(row)
        return result, replayed

    def get_task(self, owner: UUID, task_id: UUID) -> TaskRecord:
        row = self.conn.execute(
            f"SELECT {TASK_COLUMNS} FROM tasks WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (task_id, owner),
        ).fetchone()
        if row is None:
            raise not_found()
        return TaskRecord.model_validate(row)

    def list_tasks(self, owner: UUID, limit: int, offset: int) -> list[TaskRecord]:
        rows = self.conn.execute(
            f"SELECT {TASK_COLUMNS} FROM tasks WHERE user_id = %s AND deleted_at IS NULL "
            "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s", (owner, limit, offset),
        ).fetchall()
        return [TaskRecord.model_validate(row) for row in rows]

    def planning_inputs(self, owner: UUID, task_ids: tuple[UUID, ...]):
        # Keep task and preference versions from one database snapshot. End the
        # transaction before CPU scheduling; the preview does not persist writes.
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            preferences = self.get_preferences(owner)
            rows = self.conn.execute(
                f"SELECT {TASK_COLUMNS} FROM tasks WHERE user_id = %s "
                "AND id = ANY(%s::uuid[]) AND deleted_at IS NULL ORDER BY id",
                (owner, list(task_ids)),
            ).fetchall()
            if len(rows) != len(task_ids):
                # Foreign, deleted and unknown IDs have the same response.
                raise not_found()
            tasks = tuple(TaskRecord.model_validate(row) for row in rows)
        return tasks, preferences

    def update_task(self, owner: UUID, task_id: UUID, patch: TaskPatch) -> TaskRecord:
        with self.conn.transaction():
            current = self.get_task(owner, task_id)
            try:
                # PostgreSQL writes the actual audit timestamp; do not depend on app/DB clock agreement.
                prepared = prepare_task_change(current, patch, now=current.updated_at)
            except StaleTaskVersion as exc:
                raise stale_version() from exc
            except ValidationError as exc:
                raise AppError(422, "invalid_task_change", "این تغییر با مدت یا مهلت کار سازگار نیست.") from exc
            data = prepared.model_dump()
            data["expected_version"] = patch.expected_version
            row = self.conn.execute(
                "UPDATE tasks SET title = %(title)s, description = %(description)s, "
                "duration_minutes = %(duration_minutes)s, priority = %(priority)s, "
                "status = %(status)s, splittable = %(splittable)s, preferred_period = %(preferred_period)s, "
                "earliest_start = %(earliest_start)s, deadline = %(deadline)s, "
                "version = version + 1, updated_at = GREATEST(clock_timestamp(), updated_at) "
                "WHERE id = %(id)s AND user_id = %(user_id)s AND version = %(expected_version)s "
                f"AND deleted_at IS NULL RETURNING {TASK_COLUMNS}", data,
            ).fetchone()
            if row is None:
                raise stale_version()
            result = TaskRecord.model_validate(row)
        return result

    def delete_task(self, owner: UUID, task_id: UUID, expected_version: int):
        with self.conn.transaction():
            current = self.get_task(owner, task_id)
            if current.version != expected_version:
                raise stale_version()
            row = self.conn.execute(
                "UPDATE tasks SET deleted_at = clock_timestamp(), version = version + 1, "
                "updated_at = GREATEST(clock_timestamp(), updated_at) "
                "WHERE id = %s AND user_id = %s AND version = %s AND deleted_at IS NULL RETURNING id",
                (task_id, owner, expected_version),
            ).fetchone()
            if row is None:
                raise stale_version()

    def get_preferences(self, owner: UUID) -> PreferenceRecord:
        row = self.conn.execute("SELECT * FROM user_preferences WHERE user_id = %s", (owner,)).fetchone()
        if row is None:
            raise not_found()
        return PreferenceRecord.model_validate(row)

    def update_preferences(self, owner: UUID, update: PreferencePut) -> PreferenceRecord:
        data = update.values.model_dump()
        data.update(user_id=owner, expected_version=update.expected_version)
        with self.conn.transaction():
            row = self.conn.execute(
                "UPDATE user_preferences SET chronotype = %(chronotype)s, "
                "focus_block_minutes = %(focus_block_minutes)s, break_minutes = %(break_minutes)s, "
                "flexibility = %(flexibility)s, workload = %(workload)s, deep_work_period = %(deep_work_period)s, "
                "version = version + 1, updated_at = GREATEST(clock_timestamp(), updated_at) "
                "WHERE user_id = %(user_id)s AND version = %(expected_version)s RETURNING *", data,
            ).fetchone()
            if row is None:
                raise stale_version()
            result = PreferenceRecord.model_validate(row)
        return result
