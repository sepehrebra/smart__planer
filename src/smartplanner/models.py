"""Validated contracts independent of HTTP, database drivers and AI providers."""

from datetime import date, datetime, timezone
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

Version = Annotated[StrictInt, Field(ge=1, le=2_147_483_647)]
Duration = Annotated[StrictInt, Field(ge=1, le=10_080)]
Priority = Annotated[StrictInt, Field(ge=1, le=10)]
Title = Annotated[str, Field(min_length=1, max_length=200)]
Description = Annotated[str, Field(max_length=2000)]
Period = Literal["any", "morning", "afternoon", "evening"]
Status = Literal["pending", "completed", "cancelled"]


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, frozen=True
    )


class TaskTimes(Contract):
    earliest_start: AwareDatetime | None = None
    deadline: AwareDatetime | None = None

    @field_validator("earliest_start", "deadline", mode="before")
    @classmethod
    def explicit_datetimes_only(cls, value: object) -> object:
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("Use a datetime with an explicit timezone, not a numeric timestamp.")
        return value

    @field_validator("earliest_start", "deadline")
    @classmethod
    def normalize_to_utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(timezone.utc) if value is not None else None


class TaskCreate(TaskTimes):
    # Clients must reuse this ID when retrying the same creation request.
    client_request_id: UUID
    title: Title
    description: Description = ""
    duration_minutes: Duration
    priority: Priority = 5
    splittable: StrictBool = False
    preferred_period: Period = "any"

    @model_validator(mode="after")
    def validate_time_window(self) -> Self:
        if self.earliest_start is not None and self.deadline is not None:
            seconds = (self.deadline - self.earliest_start).total_seconds()
            if seconds < self.duration_minutes * 60:
                raise ValueError("The available start/deadline window is shorter than the task.")
        return self


class TaskRecord(TaskCreate):
    """Internal stored record; never use this as an untrusted create-request schema."""

    id: UUID
    user_id: UUID
    status: Status = "pending"
    version: Version = 1
    created_at: AwareDatetime
    updated_at: AwareDatetime
    recurrence_id: UUID | None = None
    occurrence_date: date | None = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_audit_time(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def audit_order(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at.")
        if (self.recurrence_id is None) != (self.occurrence_date is None):
            raise ValueError("Recurrence ID and occurrence date must appear together.")
        return self


class TaskPatch(TaskTimes):
    expected_version: Version
    title: Title | None = None
    description: Description | None = None
    duration_minutes: Duration | None = None
    priority: Priority | None = None
    splittable: StrictBool | None = None
    preferred_period: Period | None = None
    status: Status | None = None

    @model_validator(mode="after")
    def require_change_and_valid_nulls(self) -> Self:
        changed = self.model_fields_set - {"expected_version"}
        if not changed:
            raise ValueError("Supply at least one field to change.")
        nullable = {"earliest_start", "deadline"}
        for field in changed - nullable:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null.")
        return self


class PreferenceValues(Contract):
    """Six explicit scheduling preferences; these do not define availability."""

    chronotype: Literal["morning", "neutral", "evening"] = "neutral"
    focus_block_minutes: StrictInt = 45
    break_minutes: Annotated[StrictInt, Field(ge=0, le=60)] = 10
    flexibility: Literal["low", "medium", "high"] = "medium"
    workload: Literal["light", "medium", "intense"] = "medium"
    deep_work_period: Literal["morning", "afternoon", "evening"] = "afternoon"

    @field_validator("focus_block_minutes")
    @classmethod
    def supported_focus_length(cls, value: int) -> int:
        if value not in {25, 45, 60, 90}:
            raise ValueError("Choose a 25, 45, 60 or 90 minute focus block.")
        return value
