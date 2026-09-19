"""Deterministic minute-grid heuristic. Feasibility/optimality is not guaranteed.

Hard constraints never relax. New pieces of one task are committed together;
valid previous placements take precedence over soft preferences and new work.
"""

from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from math import ceil, floor
from zoneinfo import ZoneInfo

from .errors import AppError
from .models import PreferenceValues, TaskRecord
from .planning_models import PlanBlock, PlanPreview, PreviewRequest, TimeWindow, UnscheduledTask

MINUTE = timedelta(minutes=1)
MAX_CANDIDATES = 250_000
MAX_BLOCKS = 512


class SearchLimit(Exception):
    pass


def _gaps(mask: bytearray, start: int, end: int):
    index = start
    while index < end:
        if not mask[index]:
            index += 1
            continue
        left = index
        while index < end and mask[index]:
            index += 1
        yield left, index


class _Planner:
    def __init__(self, request, tasks, preferences, zone, now, budget):
        self.request, self.preferences, self.zone = request, preferences, zone
        self.now = now.astimezone(timezone.utc)
        if request.start_date < self.now.astimezone(zone).date():
            raise AppError(422, "past_planning_date", "تاریخ برنامه باید امروز یا پس از امروز باشد.")
        self.start = datetime.combine(request.start_date, time(), zone).astimezone(timezone.utc)
        self.end = datetime.combine(request.start_date + timedelta(days=request.days), time(), zone).astimezone(timezone.utc)
        self.size = int((self.end - self.start) / MINUTE)
        if not 1 <= self.size <= 8 * 1440 or self.start.astimezone(zone).date() != request.start_date:
            raise AppError(422, "invalid_local_day", "این روز در منطقهٔ زمانی انتخاب‌شده قابل برنامه‌ریزی نیست.")
        if self.start.second or self.end.second:
            raise AppError(422, "unsupported_time_boundary", "مرز روز باید با دقت دقیقه قابل نمایش باشد.")
        self.floor = min(self.size, max(0, ceil((self.now - self.start) / MINUTE)))
        self.tasks = {task.id: task for task in tasks}
        if len(self.tasks) != len(tasks) or set(self.tasks) != set(request.task_ids):
            raise ValueError("The task snapshot must exactly match the selected IDs.")
        self.pending = {key: value for key, value in self.tasks.items() if value.status == "pending"}
        self.available = bytearray(self.size)
        for window in request.availability:
            left, right = self.indices(window)
            if left < 0 or right > self.size:
                raise AppError(422, "availability_outside_horizon", "وقت آزاد باید داخل محدودهٔ روز یا هفتهٔ انتخاب‌شده باشد.")
            self.available[left:right] = b"\1" * (right - left)
        self.available[:self.floor] = b"\0" * self.floor
        self.busy = []
        for event in sorted(request.fixed_events, key=lambda event: (event.start, event.end, event.title)):
            left, right = self.indices(event)
            left, right = max(0, left), min(self.size, right)
            if left >= right:
                raise AppError(422, "event_outside_horizon", "تعهد ثابت باید با محدودهٔ برنامه اشتراک زمانی داشته باشد.")
            if self.busy and left < self.busy[-1][1]:
                raise AppError(409, "fixed_event_conflict", "دو تعهد ثابت با هم تداخل دارند؛ ساعت آن‌ها را اصلاح کنید.")
            self.busy.append((left, right))
            self.available[left:right] = b"\0" * (right - left)
        self.day_ranges = []
        self.period_prefix = {period: [0] for period in ("morning", "afternoon", "evening")}
        previous_day = None
        for index in range(self.size):
            local = (self.start + index * MINUTE).astimezone(zone)
            if local.date() != previous_day:
                if self.day_ranges:
                    self.day_ranges[-1][1] = index
                self.day_ranges.append([index, self.size])
                previous_day = local.date()
            period = ("morning" if 6 <= local.hour < 12 else "afternoon" if 12 <= local.hour < 18
                      else "evening" if local.hour >= 18 else None)
            for name, prefix in self.period_prefix.items():
                prefix.append(prefix[-1] + (period != name))
        fraction = {"light": 50, "medium": 75, "intense": 100}[preferences.workload]
        self.targets = [max(1, sum(self.available[left:right]) * fraction // 100) for left, right in self.day_ranges]
        self.blocks = []
        self.unscheduled = []
        self.budget = budget
        self.previous = defaultdict(list)
        for block in request.previous_blocks:
            if block.task_id in self.pending:
                self.previous[block.task_id].append(block)
        self.previous_start = {key: min(self.indices(block)[0] for block in blocks)
                               for key, blocks in self.previous.items()}

    def indices(self, window):
        return int((window.start - self.start) / MINUTE), int((window.end - self.start) / MINUTE)

    def bounds(self, task):
        left = max(self.floor, ceil((task.earliest_start - self.start) / MINUTE) if task.earliest_start else 0)
        right = min(self.size, floor((task.deadline - self.start) / MINUTE) if task.deadline else self.size)
        return max(0, min(self.size, left)), max(0, min(self.size, right))

    def order(self, task):
        return min(task.deadline or self.end, self.end), -task.priority, str(task.id)

    def free_mask(self, busy, pause=0):
        mask = self.available.copy()
        for left, right in busy:
            left, right = max(0, left - pause), min(self.size, right + pause)
            mask[left:right] = b"\0" * (right - left)
        return mask

    def valid_block(self, block, mask):
        left, right = self.indices(block)
        earliest, latest = self.bounds(self.pending[block.task_id])
        return earliest <= left < right <= latest and all(mask[left:right])

    def reserve(self, block):
        self.blocks.append(block)
        self.busy.append(self.indices(block))

    def preserve_previous(self):
        # Locks are validated/reserved before any movable work.
        for key in sorted(self.previous, key=str):
            task = self.pending[key]
            locks = sorted((block for block in self.previous[key] if block.locked), key=lambda block: block.start)
            locked_minutes = sum(int((block.end - block.start) / MINUTE) for block in locks)
            if locked_minutes > task.duration_minutes or (locks and not task.splittable and
                    (len(locks) != 1 or locked_minutes != task.duration_minutes)):
                raise AppError(409, "locked_duration_conflict", "مدت بلوک قفل‌شده با مدت یا اجازهٔ تقسیم کار سازگار نیست.")
            for block in locks:
                if not self.valid_block(block, self.free_mask(self.busy)):
                    raise AppError(409, "locked_block_conflict", "بلوک قفل‌شده با وقت آزاد، تعهد، مهلت یا قفل دیگری تداخل دارد.")
                self.reserve(block)
        for task in sorted(self.pending.values(), key=self.order):
            previous = self.previous[task.id]
            movable = sorted((block for block in previous if not block.locked), key=lambda block: block.start)
            total = sum(int((block.end - block.start) / MINUTE) for block in previous)
            if total != task.duration_minutes or (not task.splittable and len(previous) != 1):
                continue
            mask = self.free_mask(self.busy)
            for block in movable:
                if not self.valid_block(block, mask):
                    break
                left, right = self.indices(block)
                mask[left:right] = b"\0" * (right - left)
            else:
                for block in movable:
                    self.reserve(block)

    def usage(self, intervals):
        return [sum(max(0, min(right, end) - max(left, start)) for start, end in intervals)
                for left, right in self.day_ranges]

    def score(self, task, start, length, used):
        end = start + length
        weighted = [(task.preferred_period, 6), (self.preferences.chronotype, 2), (self.preferences.deep_work_period, 1)]
        cost = sum(weight * (self.period_prefix[period][end] - self.period_prefix[period][start])
                   for period, weight in weighted if period in self.period_prefix) * 100 // length
        for index, (left, right) in enumerate(self.day_ranges):
            added = max(0, min(end, right) - max(start, left))
            overflow = max(0, used[index] + added - self.targets[index]) - max(0, used[index] - self.targets[index])
            cost += overflow * 300 // length
        if task.id in self.previous_start:
            old = self.previous_start[task.id]
            cost += abs(start - old) * {"low": 8, "medium": 3, "high": 1}[self.preferences.flexibility] // 15
        return cost, start

    def find_slot(self, task, remaining, busy, used):
        left, right = self.bounds(task)
        target = min(remaining, self.preferences.focus_block_minutes) if task.splittable else remaining
        for pause in dict.fromkeys((self.preferences.break_minutes, 0)):
            gaps = list(_gaps(self.free_mask(busy, pause), left, right))
            longest = max((end - start for start, end in gaps), default=0)
            length = min(target, longest) if task.splittable else target
            if length == 0 or longest < length:
                continue
            best = None
            for begin, end in gaps:
                for start in range(begin, end - length + 1):
                    if self.budget <= 0:
                        raise SearchLimit
                    self.budget -= 1
                    key = self.score(task, start, length, used)
                    if best is None or key < best[0]:
                        best = key, start, length
            if best is not None:
                return best[1], best[2]
        return None

    def allocate_task(self, task):
        placed = sum(int((block.end - block.start) / MINUTE) for block in self.blocks if block.task_id == task.id)
        remaining = task.duration_minutes - placed
        if remaining == 0:
            return
        busy = list(self.busy)
        intervals = [self.indices(block) for block in self.blocks]
        additions = []
        reason = "no_slot_found"
        requested = remaining
        try:
            while remaining:
                if len(self.blocks) + len(additions) >= MAX_BLOCKS:
                    raise SearchLimit
                slot = self.find_slot(task, remaining, busy, self.usage(intervals))
                if slot is None:
                    break
                start, length = slot
                block = PlanBlock(task_id=task.id, start=self.start + start * MINUTE, end=self.start + (start + length) * MINUTE)
                additions.append(block)
                busy.append((start, start + length))
                intervals.append((start, start + length))
                remaining -= length
        except SearchLimit:
            reason = "search_limit"
        if not remaining:
            for block in additions:
                self.reserve(block)
            return
        earliest, latest = self.bounds(task)
        if task.deadline is not None and latest <= earliest:
            reason = "deadline_passed"
        messages = {
            "no_slot_found": "در این چیدمان زمان کافی پیش از مهلت پیدا نشد؛ وقت آزاد، مدت کار یا محدودهٔ برنامه را تغییر دهید.",
            "deadline_passed": "مهلت کار پیش از اولین زمان قابل استفاده است.",
            "search_limit": "سقف بررسی این پیش‌نمایش پر شد؛ تعداد کارها یا محدودهٔ برنامه را کمتر کنید.",
        }
        self.unscheduled.append(UnscheduledTask(task_id=task.id, title=task.title, remaining_minutes=requested,
                                               reason=reason, message=messages[reason]))

    def warnings(self):
        warnings = []
        chronological = sorted(self.busy)
        if self.preferences.break_minutes and any(
                0 <= following[0] - previous[1] < self.preferences.break_minutes
                for previous, following in zip(chronological, chronological[1:])):
            warnings.append("break_preference_not_met")
        used = self.usage([self.indices(block) for block in self.blocks])
        if any(minutes > target for minutes, target in zip(used, self.targets)):
            warnings.append("workload_preference_not_met")
        if any(not task.splittable and task.duration_minutes > self.preferences.focus_block_minutes
               and any(block.task_id == task.id for block in self.blocks) for task in self.pending.values()):
            warnings.append("unsplittable_task_exceeds_focus_preference")
        return tuple(warnings)


def build_preview(request: PreviewRequest, tasks: tuple[TaskRecord, ...], preferences: PreferenceValues,
                  *, timezone_name: str, now: datetime, preference_version: int = 1,
                  search_budget: int = MAX_CANDIDATES) -> PlanPreview:
    """Same validated inputs, timezone data and now produce the same preview."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware.")
    planner = _Planner(request, tasks, preferences, ZoneInfo(timezone_name), now, search_budget)
    planner.preserve_previous()
    for task in sorted(planner.pending.values(), key=planner.order):
        planner.allocate_task(task)
    return PlanPreview(
        horizon=TimeWindow(start=planner.start, end=planner.end), timezone=timezone_name,
        planned_at=planner.now, blocks=tuple(sorted(planner.blocks, key=lambda block: (block.start, str(block.task_id)))),
        fixed_events=tuple(sorted(request.fixed_events, key=lambda event: (event.start, event.end, event.title))),
        unscheduled=tuple(planner.unscheduled), ignored_task_ids=tuple(sorted(set(planner.tasks) - set(planner.pending), key=str)),
        task_versions={key: planner.tasks[key].version for key in sorted(planner.tasks, key=str)},
        preference_version=preference_version, warnings=planner.warnings(),
    )
