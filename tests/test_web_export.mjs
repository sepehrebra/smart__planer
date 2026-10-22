import assert from 'node:assert/strict';
import test from 'node:test';
import { scheduleCsv, scheduleIcs, scheduleRows } from '../src/smartplanner/web/planner_export.mjs';

const schedule = {
  id: 'schedule-1', updated_at: '2026-10-22T10:00:00Z',
  state: { task_titles: { 'task-1': '=SUM(1,2)' }, content: {
    start_date: '2026-10-22', fixed_events: [{ title: 'جلسه, مهم', start: '2026-10-22T09:00:00Z', end: '2026-10-22T10:00:00Z' }],
    blocks: [{ task_id: 'task-1', start: '2026-10-22T11:00:00Z', end: '2026-10-22T12:00:00Z' }],
  } },
};

test('exports fixed events and planned tasks in chronological order', () => {
  assert.deepEqual(scheduleRows(schedule).map(row => row.type), ['fixed', 'task']);
});

test('CSV escapes titles and prevents spreadsheet formula execution', () => {
  const csv = scheduleCsv(schedule);
  assert.ok(csv.startsWith('\uFEFF'));
  assert.match(csv, /"جلسه, مهم"/);
  assert.match(csv, /"'=SUM\(1,2\)"/);
});

test('ICS has UTC times and escaped titles', () => {
  const ics = scheduleIcs(schedule);
  assert.match(ics, /DTSTART:20261022T090000Z/);
  assert.match(ics, /SUMMARY:جلسه\\, مهم/);
  assert.equal((ics.match(/BEGIN:VEVENT/g) || []).length, 2);
});

test('ICS folds long Unicode lines on character boundaries', () => {
  const long = structuredClone(schedule);
  long.state.content.fixed_events[0].title = 'برنامه'.repeat(40);
  const ics = scheduleIcs(long);
  assert.ok(ics.split('\r\n').every(line => new TextEncoder().encode(line).length <= 75));
  assert.match(ics, /\r\n /);
});
