"""Replanning behavior with actual scheduler and mocked database boundary."""
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID
from types import SimpleNamespace
from pydantic import ValidationError
from smartplanner.schedule_models import ReplanRequest, SavedState, ScheduleContent
from smartplanner.schedule_repository import ScheduleRepository
from smartplanner.errors import AppError
from test_scheduler import DAY, NOW, PREFS, task, block, window, instant


class ReplanTests(unittest.TestCase):
    def setUp(self):
        self.tasks = (task(1), task(2))
        self.blocks = (block(self.tasks[0], '09:00', '10:00'), block(self.tasks[1], '14:00', '15:00'))
        self.state = SavedState(title='روز', content=ScheduleContent(start_date=DAY,
            task_ids=tuple(t.id for t in self.tasks), availability=(window(),), blocks=self.blocks),
            task_versions={t.id: 1 for t in self.tasks}, preference_version=1,
            task_titles={t.id: t.title for t in self.tasks}, unscheduled=(), ignored_task_ids=(), warnings=())
        self.conn = MagicMock()
        self.repo = ScheduleRepository(self.conn)
        self.repo._header = MagicMock(return_value=dict(state=self.state.model_dump(mode='json'),
            version=3, timezone='UTC', start_at=instant('00:00'), end_at=instant('23:59')))
        self.patches = [patch('smartplanner.repository.Repository'),
                        patch('smartplanner.fixed_event_repository.FixedEventRepository'),
                        patch('smartplanner.schedule_repository.datetime')]
        self.inputs, self.events, self.clock = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)
        self.inputs.return_value.get_preferences.return_value = SimpleNamespace(**PREFS.model_dump(), version=1)
        self.inputs.return_value.get_task.side_effect = lambda owner, key: next(t for t in self.tasks if t.id == key)
        self.events.return_value.list.return_value = []
        self.clock.now.return_value = NOW

    def replan(self, **changes):
        return self.repo.replan(UUID(int=999), UUID(int=100), ReplanRequest(expected_version=3, fixed_events=(), **changes))

    def test_new_commitment_moves_only_conflicting_task(self):
        self.events.return_value.list.return_value = [SimpleNamespace(title='کلاس', start=instant('09:00'), end=instant('10:00'))]
        result = self.replan()
        self.assertIn(self.blocks[1], result.preview.blocks)
        self.assertNotIn(self.blocks[0], result.preview.blocks)
        self.assertEqual(len(result.preview.blocks), 2)
        self.assertFalse(result.preview.persisted)
        self.assertEqual(result.expected_version, 3)
        self.assertTrue(all('READ ONLY' in c.args[0] for c in self.conn.execute.call_args_list))

    def test_no_conflict_preserves_layout(self):
        self.assertEqual(self.replan().preview.blocks, self.blocks)

    def test_stale_version_rejected_before_planning(self):
        with self.assertRaises(AppError) as raised:
            self.repo.replan(UUID(int=999), UUID(int=100), ReplanRequest(expected_version=2, fixed_events=()))
        self.assertEqual(raised.exception.code, 'stale_schedule_version')
        self.inputs.return_value.get_task.assert_not_called()

    def test_too_many_events_not_silently_truncated(self):
        self.events.return_value.list.return_value = [SimpleNamespace(title='کلاس', start=instant('09:00'), end=instant('10:00'))] * 101
        with self.assertRaises(AppError) as raised:
            self.replan()
        self.assertEqual(raised.exception.code, 'too_many_fixed_events')

    def test_locked_conflict_rejected(self):
        state = self.state.model_copy(update={'content': self.state.content.model_copy(update={
            'blocks': (self.blocks[0].model_copy(update={'locked': True}), self.blocks[1])})})
        self.repo._header.return_value['state'] = state.model_dump(mode='json')
        self.events.return_value.list.return_value = [SimpleNamespace(title='کلاس', start=instant('09:00'), end=instant('10:00'))]
        with self.assertRaises(AppError):
            self.replan()

    def test_adhoc_events_must_be_explicit(self):
        with self.assertRaises(ValidationError):
            ReplanRequest(expected_version=3)
