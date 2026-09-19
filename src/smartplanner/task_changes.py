"""Pure preparation of an edit. Persistence still needs atomic owner/version checks."""

from datetime import datetime, timezone

from .models import TaskPatch, TaskRecord


class StaleTaskVersion(ValueError):
    """The user edited an older task snapshot."""


def prepare_task_change(
    current: TaskRecord,
    patch: TaskPatch,
    *,
    now: datetime | None = None,
) -> TaskRecord:
    """Return a fully validated new snapshot without changing or saving current.

    The future repository MUST update with a WHERE clause containing task ID,
    authenticated owner ID and expected_version, and check the affected row count.
    This in-memory check alone is not concurrency control or authorization.
    """
    if current.version != patch.expected_version:
        raise StaleTaskVersion("The task has changed. Reload it before applying this edit.")
    data = current.model_dump()
    data.update(patch.model_dump(exclude_unset=True, exclude={"expected_version"}))
    data["version"] = current.version + 1
    data["updated_at"] = now if now is not None else datetime.now(timezone.utc)
    # Revalidate the complete task: a valid patch may conflict with unchanged fields.
    return TaskRecord.model_validate(data)
