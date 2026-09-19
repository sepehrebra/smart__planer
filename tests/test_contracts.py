"""Acceptance tests for contracts; these do not substitute for database/API tests."""

import unittest
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError

from smartplanner.models import PreferenceValues, TaskCreate, TaskPatch, TaskRecord
from smartplanner.task_changes import StaleTaskVersion, prepare_task_change


def task_input(**changes):
    data = {
        "client_request_id": str(uuid4()),
        "title": "انگلیسی",
        "duration_minutes": 60,
    }
    data.update(changes)
    return data


def record(**changes):
    data = task_input()
    data.update(
        id=uuid4(), user_id=uuid4(), version=1,
        created_at="2026-09-19T08:00:00Z", updated_at="2026-09-19T08:00:00Z",
    )
    data.update(changes)
    return TaskRecord.model_validate(data)


NOW = datetime(2026, 9, 19, 9, tzinfo=timezone.utc)


class TaskCreationTests(unittest.TestCase):
    def test_persian_task_and_optional_defaults(self):
        task = TaskCreate.model_validate(task_input(title="  انگلیسی  "))
        self.assertEqual(task.title, "انگلیسی")
        self.assertEqual(task.duration_minutes, 60)
        self.assertEqual(task.priority, 5)
        self.assertIsNone(task.deadline)

    def test_empty_or_whitespace_title_is_not_a_task(self):
        for title in ["", "  ", "\t\n", "x" * 201]:
            with self.subTest(title=title), self.assertRaises(ValidationError):
                TaskCreate.model_validate(task_input(title=title))

    def test_duration_requires_positive_integer_minutes(self):
        for duration in [0, -10, True, 1.5, "60", 10081, None]:
            with self.subTest(duration=duration), self.assertRaises(ValidationError):
                TaskCreate.model_validate(task_input(duration_minutes=duration))

    def test_priority_range_is_enforced(self):
        for priority in [0, 11, False, 1.5]:
            with self.subTest(priority=priority), self.assertRaises(ValidationError):
                TaskCreate.model_validate(task_input(priority=priority))

    def test_client_cannot_supply_owner_status_or_version(self):
        for field, value in [("user_id", str(uuid4())), ("version", 10), ("status", "completed")]:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                TaskCreate.model_validate(task_input(**{field: value}))

    def test_retry_id_must_be_valid_and_present(self):
        data = task_input()
        del data["client_request_id"]
        with self.assertRaises(ValidationError):
            TaskCreate.model_validate(data)
        with self.assertRaises(ValidationError):
            TaskCreate.model_validate(task_input(client_request_id="invalid"))

    def test_uncertain_dates_are_rejected(self):
        for value in ["tomorrow", "2026-09-20", "2026-09-20T10:00:00", 1789876800]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                TaskCreate.model_validate(task_input(deadline=value))

    def test_tehran_offset_normalizes_to_utc(self):
        task = TaskCreate.model_validate(task_input(deadline="2026-09-20T18:00:00+03:30"))
        self.assertEqual(task.deadline, datetime(2026, 9, 20, 14, 30, tzinfo=timezone.utc))

    def test_window_too_short_or_reversed_is_rejected(self):
        for end in ["2026-09-20T10:30:00Z", "2026-09-20T10:00:00Z", "2026-09-20T09:00:00Z"]:
            with self.subTest(end=end), self.assertRaises(ValidationError):
                TaskCreate.model_validate(task_input(earliest_start="2026-09-20T10:00:00Z", deadline=end))

    def test_exact_fit_across_midnight_is_valid(self):
        task = TaskCreate.model_validate(task_input(
            earliest_start="2026-09-20T23:30:00Z", deadline="2026-09-21T00:30:00Z"))
        self.assertEqual((task.deadline - task.earliest_start).total_seconds(), 3600)

    def test_explicit_dst_offsets_use_elapsed_time(self):
        # Repeated clock hour during a DST fallback: two equal clock labels can be an hour apart.
        task = TaskCreate.model_validate(task_input(
            earliest_start="2026-11-01T01:30:00-04:00", deadline="2026-11-01T01:30:00-05:00"))
        self.assertEqual((task.deadline - task.earliest_start).total_seconds(), 3600)

    def test_overdue_tasks_can_be_represented(self):
        task = TaskCreate.model_validate(task_input(deadline="2020-01-01T10:00:00Z"))
        self.assertEqual(task.deadline.year, 2020)

    def test_unknown_period_and_boolean_string_are_rejected(self):
        for change in [{"preferred_period": "middayish"}, {"splittable": "false"}]:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                TaskCreate.model_validate(task_input(**change))


class TaskEditTests(unittest.TestCase):
    def test_valid_edit_preserves_identity_and_original_snapshot(self):
        original = record()
        changed = prepare_task_change(original, TaskPatch(expected_version=1, duration_minutes=90), now=NOW)
        self.assertEqual(changed.duration_minutes, 90)
        self.assertEqual(changed.version, 2)
        self.assertEqual((changed.id, changed.user_id, changed.client_request_id),
                         (original.id, original.user_id, original.client_request_id))
        self.assertEqual(original.duration_minutes, 60)
        self.assertEqual(original.version, 1)

    def test_stale_snapshot_is_rejected(self):
        with self.assertRaises(StaleTaskVersion):
            prepare_task_change(record(version=2), TaskPatch(expected_version=1, title="ریاضی"), now=NOW)

    def test_edit_is_checked_against_existing_unchanged_fields(self):
        original = record(earliest_start="2026-09-20T10:00:00Z", deadline="2026-09-20T11:00:00Z")
        with self.assertRaises(ValidationError):
            prepare_task_change(original, TaskPatch(expected_version=1, duration_minutes=90), now=NOW)

    def test_clearing_deadline_and_increasing_duration_together_is_valid(self):
        original = record(earliest_start="2026-09-20T10:00:00Z", deadline="2026-09-20T11:00:00Z")
        changed = prepare_task_change(original, TaskPatch(expected_version=1, deadline=None, duration_minutes=90), now=NOW)
        self.assertIsNone(changed.deadline)
        self.assertEqual(changed.duration_minutes, 90)

    def test_omitted_fields_remain_unchanged(self):
        original = record(deadline="2026-09-20T11:00:00Z", priority=9)
        changed = prepare_task_change(original, TaskPatch(expected_version=1, title="واژگان انگلیسی"), now=NOW)
        self.assertEqual(changed.deadline, original.deadline)
        self.assertEqual(changed.priority, 9)

    def test_empty_patch_or_null_required_field_is_rejected(self):
        for data in [{"expected_version": 1}, {"expected_version": 1, "title": None},
                     {"expected_version": 1, "duration_minutes": None}, {"title": "math"}]:
            with self.subTest(data=data), self.assertRaises(ValidationError):
                TaskPatch.model_validate(data)

    def test_owner_and_id_cannot_be_changed_through_patch(self):
        for field in ["id", "user_id", "client_request_id", "version"]:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                TaskPatch.model_validate({"expected_version": 1, field: str(uuid4())})

    def test_completion_is_an_explicit_change(self):
        changed = prepare_task_change(record(), TaskPatch(expected_version=1, status="completed"), now=NOW)
        self.assertEqual(changed.status, "completed")


class PreferenceTests(unittest.TestCase):
    def test_six_defaults_and_valid_customization(self):
        preferences = PreferenceValues(focus_block_minutes=25, workload="light", break_minutes=5)
        self.assertEqual(len(preferences.model_dump()), 6)
        self.assertEqual(preferences.focus_block_minutes, 25)

    def test_invalid_preference_values_are_rejected(self):
        for change in [{"focus_block_minutes": 0}, {"focus_block_minutes": 35},
                       {"focus_block_minutes": True}, {"break_minutes": -1},
                       {"break_minutes": 61}, {"workload": "extreme"},
                       {"deep_work_period": "midnight"}, {"mbti": "INTJ"}]:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                PreferenceValues.model_validate(change)


if __name__ == "__main__":
    unittest.main()
