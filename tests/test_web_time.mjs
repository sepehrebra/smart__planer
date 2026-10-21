import assert from 'node:assert/strict';
import test from 'node:test';
import { addDays, availabilityForDays, dateInZone, isoToLocal, localToIso } from '../src/smartplanner/web/planner_time.mjs';

test('Tehran wall times do not depend on the device timezone', () => {
  assert.equal(localToIso('2026-10-22T09:00', 'Asia/Tehran'), '2026-10-22T05:30:00.000Z');
  assert.equal(isoToLocal('2026-10-22T05:30:00Z', 'Asia/Tehran'), '2026-10-22T09:00');
  assert.equal(dateInZone('Asia/Tehran', new Date('2026-10-21T21:00:00Z')), '2026-10-22');
});

test('weekly windows follow calendar dates through daylight saving changes', () => {
  assert.equal(addDays('2026-03-28', 1), '2026-03-29');
  const windows = availabilityForDays('2026-03-28', 7, '09:00', '18:00', 'Europe/Berlin');
  assert.equal(windows.length, 7);
  assert.equal(windows[0].start, '2026-03-28T08:00:00.000Z');
  assert.equal(windows[1].start, '2026-03-29T07:00:00.000Z');
});

test('nonexistent wall time is rejected', () => {
  assert.throws(() => localToIso('2026-03-29T02:30', 'Europe/Berlin'), RangeError);
});
