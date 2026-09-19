"""Small recurrence contracts; generated instances use the ordinary task API."""

from datetime import date, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BeforeValidator, Field, StrictBool, StrictInt, field_validator, model_validator

from .models import Contract, Description, Period, Priority, TaskRecord, Title, Version

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def calendar_day(value):
    if isinstance(value, datetime) or not isinstance(value, (str, date)):
        raise ValueError("Use a calendar date, not a timestamp.")
    if isinstance(value, str):
        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise ValueError("Use YYYY-MM-DD.")
        value = date.fromisoformat(value)
    if not date(2000, 1, 1) <= value <= date(2100, 12, 31):
        raise ValueError("Date is outside the supported range.")
    return value


CalendarDay = Annotated[date, BeforeValidator(calendar_day)]


class RecurrenceValues(Contract):
    title: Title
    description: Description = ""
    duration_minutes: Annotated[StrictInt, Field(ge=1, le=1440)]
    priority: Priority = 5
    splittable: StrictBool = False
    preferred_period: Period = "any"
    frequency: Literal["daily", "weekdays", "weekly"]
    weekdays: Annotated[tuple[Weekday, ...], Field(max_length=7)] = ()
    start_date: CalendarDay
    end_date: CalendarDay | None = None
    active: StrictBool = True

    @field_validator("weekdays")
    @classmethod
    def unique_sorted_weekdays(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Weekdays must be unique.")
        return tuple(day for day in WEEKDAYS if day in value)

    @model_validator(mode="after")
    def consistent_pattern(self) -> Self:
        if (self.frequency == "weekdays") != bool(self.weekdays):
            raise ValueError("Only selected-weekday recurrence takes a nonempty weekdays list.")
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("End date cannot precede start date.")
        return self


class RecurrenceCreate(RecurrenceValues):
    client_request_id: UUID


class RecurrenceRecord(RecurrenceCreate):
    id: UUID
    user_id: UUID
    timezone: str
    version: Version
    created_at: AwareDatetime
    updated_at: AwareDatetime


class RecurrenceReplace(Contract):
    expected_version: Version
    values: RecurrenceValues


class OccurrenceRequest(Contract):
    expected_version: Version
    start_date: CalendarDay
    days: StrictInt = 7

    @field_validator("days")
    @classmethod
    def daily_or_weekly(cls, value):
        if value not in {1, 7}:
            raise ValueError("Choose one day or seven days.")
        return value

    @field_validator("start_date")
    @classmethod
    def supported_horizon(cls, value):
        if value > date(2100, 12, 24):
            raise ValueError("Date is outside the planner's supported range.")
        return value


class OccurrenceResult(Contract):
    recurrence_id: UUID
    recurrence_version: Version
    tasks: tuple[TaskRecord, ...]
    created_count: Annotated[StrictInt, Field(ge=0, le=7)]
    deleted_dates: tuple[date, ...]
    skipped_past_dates: tuple[date, ...]
