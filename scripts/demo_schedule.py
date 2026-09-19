"""Run an in-memory example; no account, database or saved schedule is created."""

from datetime import datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from smartplanner.models import PreferenceValues, TaskRecord
from smartplanner.planning_models import FixedEvent, PreviewRequest, TimeWindow
from smartplanner.scheduler import build_preview


def main():
    zone = ZoneInfo("Asia/Tehran")
    now = datetime.now(timezone.utc)
    day = now.astimezone(zone).date() + timedelta(days=1)

    def at(hour):
        return datetime.combine(day, time(hour), zone)

    def task(number, title, duration, **changes):
        return TaskRecord(id=UUID(int=number), user_id=UUID(int=99), client_request_id=UUID(int=100 + number),
                          title=title, duration_minutes=duration, created_at=now, updated_at=now, **changes)

    english = task(1, "انگلیسی", 60, deadline=at(10))
    math = task(2, "ریاضی", 120, earliest_start=at(14))
    inputs = PreviewRequest(start_date=day, task_ids=(english.id, math.id),
                            availability=(TimeWindow(start=at(9), end=at(18)),),
                            fixed_events=(FixedEvent(title="دانشگاه", start=at(10), end=at(14)),))
    preferences = PreferenceValues(break_minutes=0, workload="intense", focus_block_minutes=90)
    preview = build_preview(inputs, (english, math), preferences, timezone_name=zone.key, now=now)
    titles = {english.id: english.title, math.id: math.title}
    rows = [(block.start, block.end, titles[block.task_id]) for block in preview.blocks]
    rows += [(event.start, event.end, event.title) for event in preview.fixed_events]
    print(f"پیش‌نمایش نمونه برای {day} به وقت تهران")
    for start, end, title in sorted(rows):
        print(f"{start.astimezone(zone):%H:%M}–{end.astimezone(zone):%H:%M}  {title}")
    for missing in preview.unscheduled:
        print(f"{missing.title}: {missing.message}")
    if "unsplittable_task_exceeds_focus_preference" in preview.warnings:
        print("توجه: ریاضی پیوسته است و از زمان تمرکز ترجیحی ۹۰ دقیقه طولانی‌تر می‌شود.")
    print("این مثال در حافظه اجرا شد؛ برنامه‌ای ذخیره نشده است.")


if __name__ == "__main__":
    main()
