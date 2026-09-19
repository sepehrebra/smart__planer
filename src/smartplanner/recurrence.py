"""Bounded, deterministic expansion in a recurrence's own local calendar."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .errors import AppError
from .recurrence_models import RecurrenceValues, WEEKDAYS


def occurrence_dates(values: RecurrenceValues, start: date, days: int) -> tuple[date, ...]:
    if days not in {1, 7}:
        raise ValueError("Expand one day or seven days at a time.")
    if not values.active:
        return ()
    selected = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        if day < values.start_date or (values.end_date is not None and day > values.end_date):
            continue
        if values.frequency == "weekly" and day.weekday() != values.start_date.weekday():
            continue
        if values.frequency == "weekdays" and WEEKDAYS[day.weekday()] not in values.weekdays:
            continue
        selected.append(day)
    return tuple(selected)


def occurrence_window(day: date, timezone_name: str, duration_minutes: int):
    zone = ZoneInfo(timezone_name)
    # Convert each local boundary independently: a local day need not be 24h.
    # fold=0 uses the first midnight when clocks repeat it.
    start = datetime.combine(day, time(), zone).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time(), zone).astimezone(timezone.utc)
    if end <= start or start.astimezone(zone).date() != day:
        raise AppError(422, "nonexistent_occurrence_day", "این روز در منطقهٔ زمانی فعالیت وجود ندارد؛ محدوده را اصلاح کنید.")
    if (end - start).total_seconds() < duration_minutes * 60:
        raise AppError(422, "occurrence_too_long", "مدت فعالیت از طول واقعی یکی از روزهای انتخاب‌شده بیشتر است.")
    return start, end
