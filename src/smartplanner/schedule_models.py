"""Contracts for exact saved layouts and bounded undo/redo history."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .models import Contract, Title, Version
from .planning_models import FixedEvent, PlanBlock, PlanPreview, PlanningWindow, TimeWindow, UnscheduledTask


class ScheduleContent(PlanningWindow):
    blocks: Annotated[tuple[PlanBlock, ...], Field(max_length=512)] = ()

    @model_validator(mode="after")
    def selected_blocks(self) -> Self:
        if any(block.task_id not in self.task_ids for block in self.blocks):
            raise ValueError("Every block must refer to a selected task.")
        return self


class ScheduleCreate(Contract):
    client_request_id: UUID
    title: Title
    content: ScheduleContent
    task_versions: Annotated[dict[UUID, Version], Field(max_length=100)]
    preference_version: Version

    @model_validator(mode="after")
    def exact_source_versions(self) -> Self:
        if set(self.task_versions) != set(self.content.task_ids):
            raise ValueError("Supply exactly the selected task versions.")
        return self


class ScheduleReplace(ScheduleCreate):
    expected_version: Version


class HistoryCommand(Contract):
    client_request_id: UUID
    expected_version: Version


class SavedState(Contract):
    title: Title
    content: ScheduleContent
    task_versions: dict[UUID, Version]
    preference_version: Version
    task_titles: dict[UUID, Title]
    unscheduled: tuple[UnscheduledTask, ...]
    ignored_task_ids: tuple[UUID, ...]
    warnings: tuple[str, ...]


class SourceStatus(Contract):
    stale: bool
    changed_task_ids: tuple[UUID, ...]
    missing_task_ids: tuple[UUID, ...]
    preferences_changed: bool


class SavedSchedule(Contract):
    id: UUID
    version: Version
    history_revision: Version
    timezone: str
    horizon: TimeWindow
    state: SavedState
    sources: SourceStatus
    can_undo: bool
    can_redo: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime
    persisted: Literal[True] = True


class ScheduleResult(Contract):
    schedule: SavedSchedule
    replayed: bool
    applied_version: Version


class ScheduleSummary(Contract):
    id: UUID
    title: Title
    version: Version
    timezone: str
    horizon: TimeWindow
    updated_at: AwareDatetime


class HistoryEntry(Contract):
    revision: Version
    title: Title
    current: bool
    created_at: AwareDatetime


class ReplanRequest(Contract):
    expected_version: Version
    # Complete list of ad-hoc events, excluding durable events loaded by server.
    fixed_events: Annotated[tuple[FixedEvent, ...], Field(max_length=100)]


class ReplanResult(Contract):
    expected_version: Version
    title: Title
    preview: PlanPreview
