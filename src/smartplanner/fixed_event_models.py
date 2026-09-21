"""Contracts for durable user-owned fixed commitments."""

from datetime import datetime
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictInt, field_validator, model_validator

from .models import Contract, Title


class FixedEventCreate(Contract):
    title: Title
    start: AwareDatetime
    end: AwareDatetime

    @field_validator("start", "end", mode="before")
    @classmethod
    def explicit_datetime(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("Use a datetime with an explicit UTC offset.")
        return value

    @model_validator(mode="after")
    def valid_window(self):
        if self.end <= self.start:
            raise ValueError("End must be after start.")
        if self.start.second or self.start.microsecond or self.end.second or self.end.microsecond:
            raise ValueError("Use whole minutes.")
        return self


class FixedEventReplace(FixedEventCreate):
    expected_version: StrictInt = Field(ge=1, le=2147483647)


class FixedEventRecord(FixedEventCreate):
    id: UUID
    version: StrictInt
    created_at: AwareDatetime
    updated_at: AwareDatetime
