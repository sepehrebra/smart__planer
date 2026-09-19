"""Bounded, explicit contracts for a preview; no schedule is persisted yet."""

from datetime import date, datetime, timezone
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictBool, StrictInt, field_validator, model_validator

from .models import Contract, Title, Version


class TimeWindow(Contract):
    start: AwareDatetime
    end: AwareDatetime

    @field_validator("start", "end", mode="before")
    @classmethod
    def explicit_time(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("Use a datetime with an explicit UTC offset.")
        return value

    @field_validator("start", "end")
    @classmethod
    def minute_in_utc(cls, value):
        try:
            value = value.astimezone(timezone.utc)
        except (OverflowError, ValueError) as exc:
            raise ValueError("Time is outside the supported range.") from exc
        if not 2000 <= value.year <= 2101 or value.second or value.microsecond:
            raise ValueError("Use whole minutes between years 2000 and 2101.")
        return value

    @model_validator(mode="after")
    def positive_window(self) -> Self:
        if self.end <= self.start:
            raise ValueError("End must be after start.")
        return self


class FixedEvent(TimeWindow):
    title: Title


class PlanBlock(TimeWindow):
    task_id: UUID
    locked: StrictBool = False


class PreviewRequest(Contract):
    start_date: date
    days: StrictInt = 1
    task_ids: Annotated[tuple[UUID, ...], Field(max_length=100)]
    availability: Annotated[tuple[TimeWindow, ...], Field(max_length=28)] = ()
    fixed_events: Annotated[tuple[FixedEvent, ...], Field(max_length=100)] = ()
    previous_blocks: Annotated[tuple[PlanBlock, ...], Field(max_length=512)] = ()

    @field_validator("start_date", mode="before")
    @classmethod
    def calendar_date_only(cls, value):
        if isinstance(value, datetime) or not isinstance(value, (str, date)):
            raise ValueError("Use a calendar date, not a timestamp.")
        if isinstance(value, str) and (len(value) != 10 or value[4] != "-" or value[7] != "-"):
            raise ValueError("Use YYYY-MM-DD.")
        return value

    @field_validator("start_date")
    @classmethod
    def supported_date(cls, value):
        if not date(2000, 1, 1) <= value <= date(2100, 12, 24):
            raise ValueError("Date is outside the supported range.")
        return value

    @field_validator("days")
    @classmethod
    def daily_or_weekly(cls, value):
        if value not in {1, 7}:
            raise ValueError("Choose one day or seven days.")
        return value

    @model_validator(mode="after")
    def unique_tasks_and_known_blocks(self) -> Self:
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("Task IDs must be unique.")
        if any(block.task_id not in self.task_ids for block in self.previous_blocks):
            raise ValueError("Previous blocks must refer to selected tasks.")
        return self


class UnscheduledTask(Contract):
    task_id: UUID
    title: str
    remaining_minutes: Annotated[StrictInt, Field(ge=1)]
    reason: Literal["deadline_passed", "no_slot_found", "search_limit"]
    message: str


class PlanPreview(Contract):
    horizon: TimeWindow
    timezone: str
    planned_at: AwareDatetime
    blocks: tuple[PlanBlock, ...]
    fixed_events: tuple[FixedEvent, ...]
    unscheduled: tuple[UnscheduledTask, ...]
    ignored_task_ids: tuple[UUID, ...]
    task_versions: dict[UUID, Version]
    preference_version: Version
    warnings: tuple[str, ...]
    persisted: Literal[False] = False
