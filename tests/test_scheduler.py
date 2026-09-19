"""Scheduling acceptance tests: hard constraints, stability and explicit failure."""

import random
import unittest
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from pydantic import ValidationError

from smartplanner.errors import AppError
from smartplanner.models import PreferenceValues, TaskRecord
from smartplanner.planning_models import FixedEvent, PlanBlock, PreviewRequest, TimeWindow
from smartplanner.scheduler import build_preview

DAY = date(2026, 9, 20)
NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)
PREFS = PreferenceValues(break_minutes=0, workload="intense", deep_work_period="morning")


def instant(clock, day="2026-09-20", offset="+00:00"):
    return datetime.fromisoformat(f"{day}T{clock}:00{offset}")


def window(start="09:00", end="18:00", **kwargs):
    return TimeWindow(start=instant(start, **kwargs), end=instant(end, **kwargs))


def task(number=1, **changes):
    data = dict(id=UUID(int=number), user_id=UUID(int=999), client_request_id=UUID(int=1000 + number),
                title=f"کار {number}", duration_minutes=60, created_at=NOW, updated_at=NOW)
    data.update(changes)
    return TaskRecord(**data)


def block(item, start, end, *, locked=False, **kwargs):
    return PlanBlock(task_id=item.id, locked=locked, start=instant(start, **kwargs), end=instant(end, **kwargs))


def request(tasks, **changes):
    data = dict(start_date=DAY, task_ids=tuple(item.id for item in tasks), availability=(window(),))
    data.update(changes)
    return PreviewRequest(**data)


def run(tasks, *, preferences=PREFS, now=NOW, zone="UTC", budget=250_000, **changes):
    return build_preview(request(tasks, **changes), tuple(tasks), preferences,
                         timezone_name=zone, now=now, search_budget=budget)


class SchedulerTests(unittest.TestCase):
    def assert_constraints(self, result, inputs, tasks):
        busy = [(item.start, item.end) for item in (*result.blocks, *result.fixed_events)]
        for index, first in enumerate(busy):
            for second in busy[index + 1:]:
                self.assertFalse(first[0] < second[1] and second[0] < first[1])
        for placed in result.blocks:
            self.assertLessEqual(result.horizon.start, placed.start)
            self.assertLessEqual(placed.end, result.horizon.end)
            cursor = placed.start
            while cursor < placed.end:
                self.assertTrue(any(item.start <= cursor < item.end for item in inputs.availability))
                cursor += timedelta(minutes=1)
        for item in tasks:
            pieces = [placed for placed in result.blocks if placed.task_id == item.id]
            minutes = sum(int((placed.end - placed.start).total_seconds() / 60) for placed in pieces)
            missing = sum(entry.remaining_minutes for entry in result.unscheduled if entry.task_id == item.id)
            self.assertEqual(minutes + missing, item.duration_minutes if item.status == "pending" else 0)
            if not item.splittable:
                self.assertLessEqual(len(pieces), 1)
            for placed in pieces:
                if item.deadline:
                    self.assertLessEqual(placed.end, item.deadline)
                if item.earliest_start:
                    self.assertGreaterEqual(placed.start, item.earliest_start)

    def test_class_and_deadline_example(self):
        english = task(1, deadline=instant("10:00"))
        math = task(2, duration_minutes=120, earliest_start=instant("14:00"))
        fixed = FixedEvent(title="دانشگاه", **window("10:00", "14:00").model_dump())
        result = run([english, math], fixed_events=(fixed,))
        self.assertEqual([(item.start, item.end) for item in result.blocks],
                         [(instant("09:00"), instant("10:00")), (instant("14:00"), instant("16:00"))])
        self.assertEqual(result.fixed_events, (fixed,))
        self.assertFalse(result.persisted)

    def test_missing_availability_never_invents_free_time(self):
        result = run([task()], availability=())
        self.assertEqual(result.blocks, ())
        self.assertEqual(result.unscheduled[0].remaining_minutes, 60)

    def test_unsplittable_task_does_not_get_a_partial_slot(self):
        result = run([task()], availability=(window("09:00", "09:50"),))
        self.assertEqual(result.blocks, ())
        self.assertEqual(result.unscheduled[0].reason, "no_slot_found")

    def test_allowed_splits_fill_separate_windows_with_exact_total(self):
        item = task(duration_minutes=90, splittable=True)
        result = run([item], availability=(window("09:00", "09:45"), window("11:00", "11:45")))
        self.assertEqual(len(result.blocks), 2)
        self.assertTrue(all((entry.end - entry.start).total_seconds() == 2700 for entry in result.blocks))
        self.assertFalse(result.unscheduled)

    def test_failed_split_rolls_back_all_new_pieces_for_later_tasks(self):
        large, small = task(1, duration_minutes=90, splittable=True, priority=10), task(2)
        result = run([large, small], availability=(window("09:00", "10:00"),))
        self.assertEqual([entry.task_id for entry in result.blocks], [small.id])
        self.assertEqual(result.unscheduled[0].remaining_minutes, 90)

    def test_earliest_start_rounds_up_and_deadline_never_rounds_up(self):
        item = task(earliest_start=instant("09:00") + timedelta(seconds=20), deadline=instant("10:01"))
        result = run([item], availability=(window("09:00", "10:01"),))
        self.assertEqual(result.blocks[0].start, instant("09:01"))
        too_late = task(deadline=instant("09:59") + timedelta(seconds=59))
        self.assertFalse(run([too_late], availability=(window("09:00", "10:00"),)).blocks)

    def test_deadline_then_priority_choose_work_when_capacity_is_short(self):
        urgent = task(1, priority=1, deadline=instant("10:00"))
        later = task(2, priority=10, deadline=instant("12:00"))
        self.assertEqual(run([later, urgent], availability=(window("09:00", "10:00"),)).blocks[0].task_id, urgent.id)
        self.assertEqual(run([task(1, priority=2), task(2, priority=9)], availability=(window("09:00", "10:00"),)).blocks[0].task_id, UUID(int=2))

    def test_explicit_afternoon_preference_influences_placement(self):
        result = run([task(preferred_period="afternoon")])
        self.assertEqual(result.blocks[0].start, instant("12:00"))

    def test_chronotype_and_deep_work_period_influence_available_choices(self):
        available = (window("09:00", "10:00"), window("18:00", "19:00"))
        for preference, clock in [(PREFS.model_copy(update={"chronotype": "evening"}), "18:00"),
                                  (PREFS.model_copy(update={"deep_work_period": "evening"}), "18:00"),
                                  (PREFS, "09:00")]:
            with self.subTest(preference=preference):
                self.assertEqual(run([task()], preferences=preference, availability=available).blocks[0].start, instant(clock))

    def test_light_workload_spreads_work_over_available_days(self):
        tasks = [task(1, priority=10), task(2)]
        available = (window("09:00", "11:00"), window("09:00", "11:00", day="2026-09-21"))
        light = run(tasks, days=7, availability=available, preferences=PREFS.model_copy(update={"workload": "light"}))
        intense = run(tasks, days=7, availability=available)
        self.assertEqual(len({item.start.date() for item in light.blocks}), 2)
        self.assertEqual(len({item.start.date() for item in intense.blocks}), 1)

    def test_breaks_are_added_when_capacity_allows(self):
        result = run([task(1), task(2)], preferences=PREFS.model_copy(update={"break_minutes": 10}), availability=(window("09:00", "12:00"),))
        self.assertEqual(result.blocks[1].start - result.blocks[0].end, timedelta(minutes=10))
        self.assertNotIn("break_preference_not_met", result.warnings)

    def test_break_preference_can_relax_but_is_reported(self):
        result = run([task(1), task(2)], preferences=PREFS.model_copy(update={"break_minutes": 10}), availability=(window("09:00", "11:00"),))
        self.assertEqual(len(result.blocks), 2)
        self.assertIn("break_preference_not_met", result.warnings)

    def test_new_event_moves_only_the_affected_task(self):
        tasks = [task(number) for number in range(1, 5)]
        old = tuple(block(item, start, end) for item, start, end in zip(tasks, ["09:00", "11:00", "18:00", "20:00"], ["10:00", "12:00", "19:00", "21:00"]))
        fixed = FixedEvent(title="جلسه تازه", **window("11:00", "12:00").model_dump())
        result = run(tasks, availability=(window("09:00", "22:00"),), previous_blocks=old, fixed_events=(fixed,))
        for unchanged in (old[0], old[2], old[3]):
            self.assertIn(unchanged, result.blocks)
        self.assertNotIn(old[1], result.blocks)
        self.assertFalse(result.unscheduled)

    def test_lock_is_preserved_despite_other_time_preferences(self):
        item = task(preferred_period="morning")
        locked = block(item, "17:00", "18:00", locked=True)
        self.assertEqual(run([item], previous_blocks=(locked,)).blocks, (locked,))

    def test_lock_conflicting_with_event_returns_actionable_error(self):
        item = task()
        with self.assertRaises(AppError) as caught:
            run([item], previous_blocks=(block(item, "09:00", "10:00", locked=True),),
                fixed_events=(FixedEvent(title="کلاس", **window("09:30", "10:30").model_dump()),))
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "locked_block_conflict"))

    def test_two_fixed_events_conflict_instead_of_silently_merging(self):
        events = tuple(FixedEvent(title="کلاس", **item.model_dump()) for item in [window("10:00", "12:00"), window("11:00", "13:00")])
        with self.assertRaises(AppError) as caught:
            run([], fixed_events=events)
        self.assertEqual(caught.exception.code, "fixed_event_conflict")

    def test_overlapping_locks_and_lock_after_deadline_are_rejected(self):
        a, b = task(1), task(2)
        with self.assertRaises(AppError):
            run([a, b], previous_blocks=(block(a, "09:00", "10:00", locked=True), block(b, "09:30", "10:30", locked=True)))
        a = task(deadline=instant("10:00"))
        with self.assertRaises(AppError):
            run([a], previous_blocks=(block(a, "11:00", "12:00", locked=True),))

    def test_invalid_locked_duration_is_rejected(self):
        for item, pieces in [(task(), [("09:00", "09:30")]),
                             (task(splittable=True), [("09:00", "11:00")]),
                             (task(), [("09:00", "09:30"), ("10:00", "10:30")])]:
            with self.subTest(item=item, pieces=pieces), self.assertRaises(AppError) as caught:
                run([item], previous_blocks=tuple(block(item, left, right, locked=True) for left, right in pieces))
            self.assertEqual(caught.exception.code, "locked_duration_conflict")

    def test_completed_and_cancelled_tasks_are_not_rescheduled(self):
        done, cancelled, pending = task(1, status="completed"), task(2, status="cancelled"), task(3)
        result = run([done, cancelled, pending], previous_blocks=(block(done, "09:00", "10:00", locked=True),))
        self.assertEqual(result.ignored_task_ids, (done.id, cancelled.id))
        self.assertEqual([item.task_id for item in result.blocks], [pending.id])

    def test_changed_duration_revalidates_previous_placement(self):
        item = task(duration_minutes=90, version=2)
        result = run([item], previous_blocks=(block(item, "09:00", "10:00"),))
        self.assertEqual(result.blocks[0].end - result.blocks[0].start, timedelta(minutes=90))
        self.assertEqual(result.task_versions[item.id], 2)

    def test_failed_remaining_split_keeps_lock_and_reports_remainder(self):
        item = task(duration_minutes=90, splittable=True)
        locked = block(item, "09:00", "09:30", locked=True)
        result = run([item], previous_blocks=(locked,), availability=(window("09:00", "10:00"),))
        self.assertEqual(result.blocks, (locked,))
        self.assertEqual(result.unscheduled[0].remaining_minutes, 60)

    def test_spring_forward_day_uses_elapsed_minutes(self):
        available = TimeWindow(start="2026-03-08T01:00:00-05:00", end="2026-03-08T04:00:00-04:00")
        result = run([task(duration_minutes=120)], start_date=date(2026, 3, 8), availability=(available,), zone="America/New_York", now=datetime(2026, 3, 7, tzinfo=timezone.utc))
        self.assertEqual(result.horizon.end - result.horizon.start, timedelta(hours=23))
        self.assertEqual(result.blocks[0].end - result.blocks[0].start, timedelta(hours=2))

    def test_fall_back_repeated_clock_hour_is_real_elapsed_time(self):
        available = TimeWindow(start="2026-11-01T01:00:00-04:00", end="2026-11-01T01:00:00-05:00")
        result = run([task()], start_date=date(2026, 11, 1), availability=(available,), zone="America/New_York", now=datetime(2026, 10, 31, tzinfo=timezone.utc))
        self.assertEqual(result.horizon.end - result.horizon.start, timedelta(hours=25))
        self.assertEqual(result.blocks[0].end - result.blocks[0].start, timedelta(hours=1))

    def test_week_across_clock_change_is_not_forced_to_168_hours(self):
        result = run([], days=7, start_date=date(2026, 3, 2), availability=(), zone="America/New_York", now=datetime(2026, 3, 1, tzinfo=timezone.utc))
        self.assertEqual(result.horizon.end - result.horizon.start, timedelta(hours=167))

    def test_cross_midnight_block_is_supported_within_week(self):
        available = TimeWindow(start="2026-09-20T23:30:00Z", end="2026-09-21T01:00:00Z")
        result = run([task(duration_minutes=90)], days=7, availability=(available,))
        self.assertEqual(result.blocks[0].end.date(), date(2026, 9, 21))

    def test_past_deadline_is_reported_and_not_silently_extended(self):
        result = run([task(deadline=instant("08:00", day="2026-09-19"))])
        self.assertEqual(result.blocks, ())
        self.assertEqual(result.unscheduled[0].reason, "deadline_passed")

    def test_today_never_allocates_elapsed_minutes(self):
        result = run([task()], now=instant("09:30") + timedelta(seconds=30), availability=(window("09:00", "12:00"),))
        self.assertEqual(result.blocks[0].start, instant("09:31"))

    def test_past_local_date_and_skipped_calendar_day_are_rejected(self):
        with self.assertRaises(AppError):
            run([], now=instant("23:00"), zone="Asia/Tokyo", availability=())
        with self.assertRaises(AppError):
            run([], start_date=date(2011, 12, 30), now=datetime(2011, 12, 28, tzinfo=timezone.utc), zone="Pacific/Apia", availability=())

    def test_fractional_timezone_offset_is_respected(self):
        available = window("09:00", "10:00", offset="+05:45")
        result = run([task()], availability=(available,), zone="Asia/Kathmandu")
        self.assertEqual(result.blocks[0].start, instant("03:15"))

    def test_search_budget_preserves_existing_blocks_and_returns_missing_work(self):
        a, b = task(1), task(2)
        old = block(a, "09:00", "10:00")
        result = run([a, b], previous_blocks=(old,), budget=0)
        self.assertEqual(result.blocks, (old,))
        self.assertEqual(result.unscheduled[0].reason, "search_limit")

    def test_overlapping_availability_is_a_union_not_double_capacity(self):
        item = task(duration_minutes=90)
        available = (window("09:00", "10:00"), window("09:30", "11:00"))
        result = run([item], availability=available)
        self.assertEqual(len(result.blocks), 1)
        result = run([task(duration_minutes=150)], availability=available)
        self.assertEqual(result.blocks, ())

    def test_out_of_horizon_availability_and_irrelevant_event_are_rejected(self):
        with self.assertRaises(AppError):
            run([], availability=(window(day="2026-09-21"),))
        with self.assertRaises(AppError):
            run([], fixed_events=(FixedEvent(title="فردای محدوده", **window(day="2026-09-21").model_dump()),))

    def test_event_crossing_horizon_boundary_keeps_original_times(self):
        event = FixedEvent(title="سفر", start="2026-09-19T22:00:00Z", end="2026-09-20T10:00:00Z")
        result = run([task()], fixed_events=(event,))
        self.assertEqual(result.fixed_events, (event,))
        self.assertGreaterEqual(result.blocks[0].start, event.end)

    def test_input_order_does_not_change_equal_score_schedule(self):
        tasks = [task(3), task(1), task(2, splittable=True)]
        available = (window("09:00", "12:00"), window("14:00", "18:00"))
        self.assertEqual(run(tasks, availability=available), run(tasks[::-1], availability=available[::-1]))

    def test_randomized_hard_constraints_and_duration_accounting(self):
        rng = random.Random(712)
        for scenario in range(30):
            tasks = [task(number, duration_minutes=rng.randint(1, 180), priority=rng.randint(1, 10),
                          splittable=rng.choice([True, False]), deadline=instant(rng.choice(["11:00", "16:00", "18:00"])))
                     for number in range(1, 9)]
            inputs = request(tasks, availability=(window("09:00", "12:00"), window("13:00", "18:00")),
                             fixed_events=(FixedEvent(title="کلاس", **window("14:00", "15:00").model_dump()),))
            with self.subTest(scenario=scenario):
                result = build_preview(inputs, tuple(tasks), PREFS, timezone_name="UTC", now=NOW)
                self.assert_constraints(result, inputs, tasks)


class PreviewContractTests(unittest.TestCase):
    def test_bad_window_shapes_are_rejected(self):
        for start, end in [(1, 2), ("2026-09-20T09:00", "2026-09-20T10:00"),
                           ("2026-09-20T09:00:01Z", "2026-09-20T10:00:00Z"),
                           (instant("10:00"), instant("09:00"))]:
            with self.subTest(start=start), self.assertRaises(ValidationError):
                TimeWindow(start=start, end=end)

    def test_bad_day_count_or_date_is_rejected(self):
        for change in [{"days": True}, {"days": 2}, {"days": "7"}, {"start_date": 1789876800},
                       {"start_date": "9999-12-31"}, {"start_date": "2026-09-20T00:00:00Z"}]:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                request([], **change)

    def test_duplicate_ids_foreign_blocks_and_excessive_size_are_rejected(self):
        for change in [{"task_ids": (task().id, task().id)},
                       {"previous_blocks": (block(task(2), "09:00", "10:00"),)},
                       {"task_ids": tuple(UUID(int=index) for index in range(101))},
                       {"user_id": UUID(int=9)}, {"timezone": "Europe/Paris"}]:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                request([task()], **change)


if __name__ == "__main__":
    unittest.main()
