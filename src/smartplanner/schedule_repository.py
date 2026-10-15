"""Atomic saved layouts, stable optimistic versions, and durable request receipts."""

import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from .accounts import PreferenceRecord
from .errors import AppError, not_found
from .models import TaskRecord
from .planning_models import FixedEvent, PreviewRequest, TimeWindow
from .repository import TASK_COLUMNS
from .schedule_models import (
    HistoryEntry, SavedSchedule, SavedState, ScheduleResult, ScheduleSummary, SourceStatus,
)
from .scheduler import inspect_layout

HISTORY_LIMIT = 20


def command_digest(operation, schedule_id, data):
    value = {"operation": operation, "schedule_id": str(schedule_id) if schedule_id else None,
             "request": data.model_dump(mode="json")}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def stale_sources():
    return AppError(409, "schedule_sources_changed", "کارها یا ترجیحات تغییر کرده‌اند؛ اطلاعات تازه را بگیرید و دوباره برنامه را بررسی کنید.")


def block_key(block):
    return block.task_id, block.start, block.end


def protect_placements(previous, candidate, live_rows, now):
    old_keys = {block_key(block) for block in previous}
    new_keys = {block_key(block) for block in candidate}
    for block in previous:
        live = live_rows.get(block.task_id)
        if live is None or live["deleted_at"] is not None or live["status"] != "pending":
            continue
        if block_key(block) not in new_keys:
            if block.locked:
                raise AppError(409, "locked_block_conflict", "ابتدا قفل کار را در یک تغییر جدا باز کنید؛ سپس زمان آن را تغییر دهید.")
            if block.start < now:
                raise AppError(409, "started_block_conflict", "زمان کار شروع‌شده را نمی‌توان جابه‌جا یا حذف کرد.")
    if any(block.start < now and block_key(block) not in old_keys for block in candidate):
        raise AppError(409, "past_block", "بلوک تازه یا جابه‌جاشده نباید در گذشته شروع شود.")


class ScheduleRepository:
    def __init__(self, connection):
        self.conn = connection

    def _owner_lock(self, owner):
        row = self.conn.execute("SELECT timezone FROM users WHERE id=%s FOR NO KEY UPDATE", (owner,)).fetchone()
        if row is None:
            raise not_found()
        return row["timezone"]

    def _header(self, owner, schedule_id):
        row = self.conn.execute(
            "SELECT s.*, v.state, "
            "EXISTS(SELECT 1 FROM schedule_versions h WHERE h.schedule_id=s.id AND h.revision<s.current_revision) AS can_undo, "
            "EXISTS(SELECT 1 FROM schedule_versions h WHERE h.schedule_id=s.id AND h.revision>s.current_revision) AS can_redo "
            "FROM schedules s JOIN schedule_versions v ON v.schedule_id=s.id AND v.revision=s.current_revision "
            "WHERE s.id=%s AND s.user_id=%s", (schedule_id, owner),
        ).fetchone()
        if row is None:
            raise not_found()
        return row

    def _view(self, owner, schedule_id):
        row = self._header(owner, schedule_id)
        state = SavedState.model_validate(row["state"])
        # One statement observes task and preference freshness together, even for
        # idempotent replay within the read-committed write transaction.
        sources = self.conn.execute(
            "SELECT p.version AS preference_version, t.id, t.version, t.deleted_at FROM user_preferences p "
            "LEFT JOIN tasks t ON t.user_id=p.user_id AND t.id=ANY(%s::uuid[]) WHERE p.user_id=%s",
            (list(state.task_versions), owner),
        ).fetchall()
        live = {item["id"]: item for item in sources if item["id"] is not None}
        missing = tuple(sorted((key for key in state.task_versions if key not in live or live[key]["deleted_at"] is not None), key=str))
        changed = tuple(sorted((key for key, version in state.task_versions.items()
                                if key in live and key not in missing and live[key]["version"] != version), key=str))
        preferences_changed = not sources or sources[0]["preference_version"] != state.preference_version
        return SavedSchedule(id=row["id"], version=row["version"], history_revision=row["current_revision"],
                             timezone=row["timezone"], horizon=TimeWindow(start=row["start_at"], end=row["end_at"]),
                             state=state, sources=SourceStatus(stale=bool(missing or changed or preferences_changed),
                             missing_task_ids=missing, changed_task_ids=changed, preferences_changed=preferences_changed),
                             can_undo=row["can_undo"], can_redo=row["can_redo"], created_at=row["created_at"], updated_at=row["updated_at"])

    def get(self, owner, schedule_id):
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            result = self._view(owner, schedule_id)
        return result

    def replan(self, owner, schedule_id, data):
        """Read one coherent snapshot; CPU work and explicit save stay separate."""
        from .fixed_event_repository import FixedEventRepository
        from .repository import Repository
        from .schedule_models import ReplanResult
        from .scheduler import build_preview

        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            header = self._header(owner, schedule_id)
            self._next_version(header, data.expected_version)
            previous = SavedState.model_validate(header["state"])
            repo = Repository(self.conn)
            preferences = repo.get_preferences(owner)
            tasks = tuple(repo.get_task(owner, key) for key in previous.content.task_ids)
            # Fetch one extra record to detect the bound instead of silently truncating.
            stored = FixedEventRepository(self.conn).list(
                owner, start=header["start_at"], end=header["end_at"], limit=101)
            events = tuple(FixedEvent(title=e.title, start=e.start, end=e.end) for e in stored) + data.fixed_events
            if len(events) > 100:
                raise AppError(422, "too_many_fixed_events", "تعداد تعهدهای ثابت در این بازه بیش از حد مجاز است.")
            request = PreviewRequest(
                start_date=previous.content.start_date, days=previous.content.days,
                task_ids=previous.content.task_ids, availability=previous.content.availability,
                fixed_events=events, previous_blocks=previous.content.blocks)
        preview = build_preview(request, tasks, preferences, timezone_name=header["timezone"],
                                now=datetime.now(timezone.utc), preference_version=preferences.version)
        return ReplanResult(expected_version=header["version"], title=previous.title, preview=preview)

    def list(self, owner, limit=20, offset=0):
        rows = self.conn.execute(
            "SELECT s.id, s.version, s.timezone, s.start_at, s.end_at, s.updated_at, v.state->>'title' AS title "
            "FROM schedules s JOIN schedule_versions v ON v.schedule_id=s.id AND v.revision=s.current_revision "
            "WHERE s.user_id=%s ORDER BY s.updated_at DESC, s.id DESC LIMIT %s OFFSET %s", (owner, limit, offset),
        ).fetchall()
        return [ScheduleSummary(id=row["id"], title=row["title"], version=row["version"], timezone=row["timezone"],
                                horizon=TimeWindow(start=row["start_at"], end=row["end_at"]), updated_at=row["updated_at"]) for row in rows]

    def history(self, owner, schedule_id):
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            header = self._header(owner, schedule_id)
            rows = self.conn.execute("SELECT revision, state->>'title' AS title, created_at FROM schedule_versions "
                                     "WHERE schedule_id=%s AND user_id=%s ORDER BY revision", (schedule_id, owner)).fetchall()
            result = [HistoryEntry(**row, current=row["revision"] == header["current_revision"]) for row in rows]
        return result

    def _replay(self, owner, request_id, digest):
        receipt = self.conn.execute("SELECT * FROM schedule_commands WHERE user_id=%s AND client_request_id=%s",
                                    (owner, request_id)).fetchone()
        if receipt is None:
            return None
        if receipt["request_digest"] != digest:
            raise AppError(409, "request_id_conflict", "شناسهٔ این درخواست قبلاً برای عمل یا محتوای دیگری استفاده شده است.")
        return ScheduleResult(schedule=self._view(owner, receipt["schedule_id"]), replayed=True,
                              applied_version=receipt["applied_version"])

    def _sources(self, owner, candidate, previous=None):
        preferences = self.conn.execute("SELECT * FROM user_preferences WHERE user_id=%s FOR SHARE", (owner,)).fetchone()
        ids = set(candidate.content.task_ids) | (set(previous.content.task_ids) if previous else set())
        rows = self.conn.execute(f"SELECT {TASK_COLUMNS}, deleted_at FROM tasks WHERE user_id=%s "
                                 "AND id=ANY(%s::uuid[]) ORDER BY id FOR SHARE", (owner, list(ids))).fetchall()
        live = {row["id"]: row for row in rows}
        if preferences is None or preferences["version"] != candidate.preference_version:
            raise stale_sources()
        for key, version in candidate.task_versions.items():
            if key not in live or live[key]["deleted_at"] is not None or live[key]["version"] != version:
                raise stale_sources()
        tasks = tuple(TaskRecord.model_validate({key: value for key, value in live[task_id].items() if key != "deleted_at"})
                      for task_id in candidate.content.task_ids)
        return tasks, PreferenceRecord.model_validate(preferences), live

    def _validate(self, owner, candidate, zone, previous=None):
        tasks, preferences, live = self._sources(owner, candidate, previous)
        # Obtain the clock after waiting for database locks.
        now = datetime.now(timezone.utc)
        if previous is None and candidate.content.start_date < now.astimezone(ZoneInfo(zone)).date():
            raise AppError(422, "past_planning_date", "برنامهٔ تازه باید از امروز یا روزی در آینده شروع شود.")
        if previous is not None and (candidate.content.start_date, candidate.content.days) != (previous.content.start_date, previous.content.days):
            raise AppError(409, "schedule_horizon_changed", "تاریخ و طول محدودهٔ برنامهٔ ذخیره‌شده ثابت است.")
        protect_placements(previous.content.blocks if previous else (), candidate.content.blocks, live, now)
        horizon, unscheduled, ignored, warnings = inspect_layout(candidate.content, candidate.content.blocks, tasks, preferences, timezone_name=zone)
        if any(event.start < horizon.start or event.end > horizon.end for event in candidate.content.fixed_events):
            raise AppError(422, "fixed_event_outside_saved_horizon", "تعهد ثابت باید کامل داخل محدودهٔ ذخیره باشد؛ برنامهٔ هفتگی انتخاب کنید یا تعهد را در هر دو روز ثبت کنید.")
        state = SavedState(title=candidate.title, content=candidate.content, task_versions=candidate.task_versions,
                           preference_version=candidate.preference_version, task_titles={task.id: task.title for task in tasks},
                           unscheduled=unscheduled, ignored_task_ids=ignored, warnings=warnings)
        return state, horizon

    def _allocations(self, owner, schedule_id, state):
        ids = sorted({block.task_id for block in state.content.blocks}, key=str)
        conflict = self.conn.execute("SELECT 1 FROM schedule_allocations WHERE user_id=%s AND task_id=ANY(%s::uuid[]) "
                                     "AND schedule_id<>%s LIMIT 1", (owner, ids, schedule_id)).fetchone()
        if conflict:
            raise AppError(409, "task_already_scheduled", "این کار در برنامهٔ دیگری زمان دارد؛ ابتدا زمان قبلی آن را بردارید.")
        self.conn.execute("DELETE FROM schedule_allocations WHERE user_id=%s AND schedule_id=%s", (owner, schedule_id))
        if ids:
            self.conn.execute("INSERT INTO schedule_allocations(user_id,task_id,schedule_id) "
                              "SELECT %s, task_id, %s FROM unnest(%s::uuid[]) AS selected(task_id)",
                              (owner, schedule_id, ids))

    def _insert_state(self, owner, schedule_id, revision, state):
        self.conn.execute("INSERT INTO schedule_versions(schedule_id,user_id,revision,state) VALUES (%s,%s,%s,%s)",
                          (schedule_id, owner, revision, Jsonb(state.model_dump(mode="json"))))

    def _finish(self, owner, schedule_id, request_id, digest, version):
        self.conn.execute("INSERT INTO schedule_commands(user_id,client_request_id,schedule_id,request_digest,applied_version) "
                          "VALUES (%s,%s,%s,%s,%s)", (owner, request_id, schedule_id, digest, version))
        return ScheduleResult(schedule=self._view(owner, schedule_id), replayed=False, applied_version=version)

    @staticmethod
    def _next_version(header, expected):
        if header["version"] != expected:
            raise AppError(409, "stale_schedule_version", "برنامه در جای دیگری تغییر کرده است؛ نسخهٔ تازه را دریافت کنید.")
        if expected >= 2_147_483_647:
            raise AppError(409, "schedule_version_limit", "این برنامه به سقف تعداد تغییر رسیده است.")
        return expected + 1

    def create(self, owner, data):
        digest = command_digest("create", None, data)
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ WRITE")
            zone = self._owner_lock(owner)
            result = self._replay(owner, data.client_request_id, digest)
            if result is None:
                state, horizon = self._validate(owner, data, zone)
                if self.conn.execute("SELECT 1 FROM schedules WHERE user_id=%s AND start_at<%s AND end_at>%s LIMIT 1",
                                     (owner, horizon.end, horizon.start)).fetchone():
                    raise AppError(409, "schedule_overlap", "برای بخشی از این بازه برنامه دارید؛ همان برنامه را ویرایش کنید.")
                schedule_id = uuid4()
                self.conn.execute("INSERT INTO schedules(id,user_id,start_at,end_at,timezone) VALUES (%s,%s,%s,%s,%s)",
                                  (schedule_id, owner, horizon.start, horizon.end, zone))
                self._insert_state(owner, schedule_id, 1, state)
                self._allocations(owner, schedule_id, state)
                result = self._finish(owner, schedule_id, data.client_request_id, digest, 1)
        return result

    def replace(self, owner, schedule_id, data):
        digest = command_digest("replace", schedule_id, data)
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ WRITE")
            self._owner_lock(owner)
            header = self._header(owner, schedule_id)
            result = self._replay(owner, data.client_request_id, digest)
            if result is None:
                version = self._next_version(header, data.expected_version)
                previous = SavedState.model_validate(header["state"])
                state, _ = self._validate(owner, data, header["timezone"], previous)
                self._allocations(owner, schedule_id, state)
                self.conn.execute("DELETE FROM schedule_versions WHERE schedule_id=%s AND revision>%s", (schedule_id, header["current_revision"]))
                self._insert_state(owner, schedule_id, version, state)
                self.conn.execute("UPDATE schedules SET version=%s,current_revision=%s,updated_at=GREATEST(clock_timestamp(),updated_at) "
                                  "WHERE id=%s AND user_id=%s", (version, version, schedule_id, owner))
                self.conn.execute("DELETE FROM schedule_versions WHERE schedule_id=%s AND revision NOT IN "
                                  "(SELECT revision FROM schedule_versions WHERE schedule_id=%s ORDER BY revision DESC LIMIT %s)",
                                  (schedule_id, schedule_id, HISTORY_LIMIT))
                result = self._finish(owner, schedule_id, data.client_request_id, digest, version)
        return result

    def travel(self, owner, schedule_id, data, direction):
        if direction not in {"undo", "redo"}:
            raise ValueError("Unknown history direction.")
        digest = command_digest(direction, schedule_id, data)
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ WRITE")
            self._owner_lock(owner)
            header = self._header(owner, schedule_id)
            result = self._replay(owner, data.client_request_id, digest)
            if result is None:
                version = self._next_version(header, data.expected_version)
                comparison, order = ("<", "DESC") if direction == "undo" else (">", "ASC")
                target = self.conn.execute("SELECT revision,state FROM schedule_versions WHERE schedule_id=%s "
                                           f"AND revision{comparison}%s ORDER BY revision {order} LIMIT 1",
                                           (schedule_id, header["current_revision"])).fetchone()
                if target is None:
                    raise AppError(409, "history_boundary", "در این جهت نسخهٔ دیگری برای برگشت وجود ندارد.")
                previous, destination = SavedState.model_validate(header["state"]), SavedState.model_validate(target["state"])
                self._validate(owner, destination, header["timezone"], previous)
                self._allocations(owner, schedule_id, destination)
                self.conn.execute("UPDATE schedules SET version=%s,current_revision=%s,updated_at=GREATEST(clock_timestamp(),updated_at) "
                                  "WHERE id=%s AND user_id=%s", (version, target["revision"], schedule_id, owner))
                result = self._finish(owner, schedule_id, data.client_request_id, digest, version)
        return result
