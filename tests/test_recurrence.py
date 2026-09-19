"""Calendar semantics and input boundaries, independent of storage."""

import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from smartplanner.errors import AppError
from smartplanner.recurrence import occurrence_dates, occurrence_window
from smartplanner.recurrence_models import OccurrenceRequest, RecurrenceValues


class RecurrenceTests(unittest.TestCase):
    def values(self, **changes):
        return RecurrenceValues(title="انگلیسی", duration_minutes=60, frequency="daily",
                                start_date="2026-09-19", **changes)

    def test_daily_respects_start_and_inclusive_end(self):
        values = self.values(end_date="2026-09-21")
        self.assertEqual(occurrence_dates(values, date(2026, 9, 17), 7),
                         (date(2026, 9, 19), date(2026, 9, 20), date(2026, 9, 21)))

    def test_selected_saturday_and_monday(self):
        values = RecurrenceValues(title="English", duration_minutes=60, frequency="weekdays",
                                  weekdays=("sat", "mon"), start_date="2026-09-19")
        self.assertEqual(occurrence_dates(values, date(2026, 9, 19), 7),
                         (date(2026, 9, 19), date(2026, 9, 21)))
        self.assertEqual(values.weekdays, ("mon", "sat"))

    def test_weekly_uses_start_weekday_even_when_range_starts_later(self):
        values = RecurrenceValues(title="Exercise", duration_minutes=30, frequency="weekly", start_date="2026-09-19")
        self.assertEqual(occurrence_dates(values, date(2026, 9, 22), 7), (date(2026, 9, 26),))

    def test_leap_day_and_year_boundary(self):
        values = RecurrenceValues(title="Read", duration_minutes=10, frequency="daily", start_date="2024-02-28")
        self.assertIn(date(2024, 2, 29), occurrence_dates(values, date(2024, 2, 28), 7))
        self.assertIn(date(2025, 1, 1), occurrence_dates(values, date(2024, 12, 30), 7))

    def test_paused_and_out_of_range_have_no_new_dates(self):
        self.assertEqual(occurrence_dates(self.values(active=False), date(2026, 9, 19), 7), ())
        self.assertEqual(occurrence_dates(self.values(end_date="2026-09-20"), date(2026, 9, 21), 7), ())

    def test_invalid_patterns_and_fields_are_rejected(self):
        base = self.values().model_dump()
        for changes in ({"frequency": "monthly"}, {"frequency": "weekdays"}, {"weekdays": ["mon"]},
                        {"frequency": "weekdays", "weekdays": ["mon", "mon"]},
                        {"frequency": "weekdays", "weekdays": [0]}, {"duration_minutes": 1441},
                        {"duration_minutes": True}, {"active": 1}, {"end_date": "2026-09-18"},
                        {"timezone": "UTC"}, {"user_id": "forged"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                RecurrenceValues.model_validate({**base, **changes})

    def test_dates_are_calendar_dates_and_expansion_is_bounded(self):
        for value in (0, True, datetime(2026, 9, 19), "2026-09-19T00:00:00Z", "2026-9-19", "2101-01-01"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                OccurrenceRequest(expected_version=1, start_date=value)
        for changes in ({"days": True}, {"days": 365}, {"expected_version": True}, {"start_date": "2100-12-25"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                OccurrenceRequest.model_validate({"expected_version": 1, "start_date": "2026-09-19", **changes})
        with self.assertRaises(ValueError):
            occurrence_dates(self.values(), date(2026, 9, 19), 365)

    def test_local_day_uses_tehran_and_45_minute_offset(self):
        day = date(2026, 9, 19)
        for name, hour, minute in (("Asia/Tehran", 20, 30), ("Asia/Kathmandu", 18, 15)):
            with self.subTest(zone=name):
                start, end = occurrence_window(day, name, 60)
                self.assertEqual(start, datetime(2026, 9, 18, hour, minute, tzinfo=timezone.utc))
                self.assertEqual(end - start, timedelta(hours=24))
                self.assertEqual(start.astimezone(ZoneInfo(name)).date(), day)

    def test_spring_and_fall_days_have_real_duration(self):
        for day, hours in ((date(2026, 3, 8), 23), (date(2026, 11, 1), 25)):
            with self.subTest(day=day):
                start, end = occurrence_window(day, "America/New_York", 60)
                self.assertEqual(end - start, timedelta(hours=hours))
        with self.assertRaises(AppError) as caught:
            occurrence_window(date(2026, 3, 8), "America/New_York", 1440)
        self.assertEqual(caught.exception.code, "occurrence_too_long")

    def test_missing_and_repeated_midnight(self):
        start, end = occurrence_window(date(2026, 9, 6), "America/Santiago", 60)
        self.assertEqual(start.astimezone(ZoneInfo("America/Santiago")).hour, 1)
        self.assertEqual(end - start, timedelta(hours=23))
        start, end = occurrence_window(date(2026, 11, 1), "America/Havana", 60)
        self.assertEqual(start.astimezone(ZoneInfo("America/Havana")).fold, 0)
        self.assertEqual(end - start, timedelta(hours=25))

    def test_entire_skipped_date_is_not_shifted_into_tomorrow(self):
        with self.assertRaises(AppError) as caught:
            occurrence_window(date(2011, 12, 30), "Pacific/Apia", 30)
        self.assertEqual(caught.exception.code, "nonexistent_occurrence_day")


if __name__ == "__main__":
    unittest.main()
