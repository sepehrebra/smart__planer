import { test, expect } from 'playwright/test';
import { readFile } from 'node:fs/promises';

const password = 'e2e-test-password-2026';
const tomorrow = () => new Date(Date.now() + 3 * 86400000).toISOString().slice(0, 10);

async function register(page, label) {
  const email = `${label}-${Date.now()}@example.com`;
  await page.goto('/');
  await page.locator('#register-email').fill(email);
  await page.locator('#register-password').fill(password);
  await page.locator('#timezone').selectOption('UTC');
  await page.locator('#register-form button').click();
  await expect(page.locator('#planner')).toBeVisible();
  await expect(page.locator('#welcome')).toContainText(email);
  return email;
}

async function addTask(page, title, duration = 60) {
  await page.locator('#task-title').fill(title);
  await page.locator('#task-duration').fill(String(duration));
  await page.locator('#task-form button').first().click();
  await expect(page.locator('#tasks .item', { hasText: title })).toBeVisible();
}

test('browser account, task lifecycle, isolation and session', async ({ browser }) => {
  const first = await browser.newPage();
  const email = await register(first, 'owner');
  await addTask(first, 'ریاضی', 120);
  const ownerTask = await first.evaluate(async () => (await (await fetch('/api/v1/tasks')).json())[0]);
  await first.reload();
  await expect(first.locator('#tasks .item', { hasText: 'ریاضی' })).toBeVisible();
  await first.locator('#tasks .item', { hasText: 'ریاضی' }).getByRole('button', { name: 'ویرایش' }).click();
  await first.locator('#task-title').fill('ریاضی پیشرفته');
  await first.locator('#task-status').selectOption('completed');
  await first.locator('#task-form button').first().click();
  await expect(first.locator('#tasks .item', { hasText: 'ریاضی پیشرفته' })).toContainText('completed');
  const second = await browser.newPage();
  await register(second, 'other');
  await expect(second.locator('#tasks')).not.toContainText('ریاضی پیشرفته');
  expect((await second.context().request.get(`/api/v1/tasks/${ownerTask.id}`)).status()).toBe(404);
  const csrf = await first.evaluate(async () => (await (await fetch('/api/v1/auth/session')).json()).csrf_token);
  const missingCsrf = await first.evaluate(async () => (await fetch('/api/v1/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ client_request_id: crypto.randomUUID(), title: 'Rejected', duration_minutes: 30 }) })).status);
  expect(missingCsrf).toBe(403);
  const stale = await first.evaluate(async ({ id, token }) => (await fetch(`/api/v1/tasks/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token }, body: JSON.stringify({ expected_version: 1, title: 'Stale' }) })).status, { id: ownerTask.id, token: csrf });
  expect(stale).toBe(409);
  await first.getByRole('button', { name: 'خروج' }).click();
  await expect(first.locator('#auth')).toBeVisible();
  await first.locator('#login-email').fill(email);
  await first.locator('#login-password').fill('wrong-password');
  await first.locator('#login-form button').click();
  await expect(first.locator('#notice')).toContainText('درست نیست');
  await first.locator('#login-password').fill(password);
  await first.locator('#login-form button').click();
  await expect(first.locator('#tasks .item', { hasText: 'ریاضی پیشرفته' })).toBeVisible();
  first.once('dialog', dialog => dialog.accept());
  await first.locator('#tasks .item', { hasText: 'ریاضی پیشرفته' }).getByRole('button', { name: 'حذف' }).click();
  await expect(first.locator('#tasks')).not.toContainText('ریاضی پیشرفته');
  await first.close(); await second.close();
});

test('browser daily and weekly scheduling, editing, undo, redo and exports', async ({ page }) => {
  await register(page, 'schedule');
  const day = tomorrow();
  await addTask(page, 'Mathematics', 120);
  await addTask(page, 'English', 60);
  await addTask(page, 'Programming', 90);
  await addTask(page, 'Exercise', 45);
  await page.locator('#event-title').fill('University');
  await page.locator('#event-start').fill(`${day}T10:00`);
  await page.locator('#event-end').fill(`${day}T14:00`);
  await page.locator('#event-form button').first().click();
  await expect(page.locator('#events .item', { hasText: 'University' })).toBeVisible();
  await page.locator('#schedule-date').fill(day);
  await page.locator('#schedule-days').selectOption('1');
  for (const checkbox of await page.locator('#schedule-tasks input[type="checkbox"]').all()) await checkbox.check();
  const dailyResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/schedules/preview') && response.request().method() === 'POST');
  await page.locator('#schedule-form button').click();
  const daily = await (await dailyResponse).json();
  expect(daily.fixed_events.some(event => event.title === 'University')).toBe(true);
  expect(daily.unscheduled.length).toBeGreaterThan(0);
  const occupied = [...daily.blocks, ...daily.fixed_events].sort((a, b) => a.start.localeCompare(b.start));
  for (let index = 1; index < occupied.length; index++) expect(occupied[index - 1].end <= occupied[index].start).toBe(true);
  await expect(page.locator('.unscheduled')).toBeVisible();
  await page.locator('#schedule-days').selectOption('7');
  const weeklyResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/schedules/preview') && response.request().method() === 'POST');
  await page.locator('#schedule-form button').click();
  const weekly = await (await weeklyResponse).json();
  expect(weekly.blocks.length).toBeGreaterThan(0);
  await expect(page.locator('.timeline h4')).toHaveCount(7);
  await page.locator('#save-schedule-form button').click();
  await expect(page.locator('#saved-schedule')).toBeVisible();
  const beforeMove = await page.locator('#block-start').inputValue();
  const beforeEnd = await page.locator('#block-end').inputValue();
  const duration = (Date.parse(beforeEnd) - Date.parse(beforeMove)) / 60000;
  const stored = await page.evaluate(async () => {
    const summaries = await (await fetch('/api/v1/schedules')).json();
    return (await (await fetch(`/api/v1/schedules/${summaries[0].id}`)).json());
  });
  const content = stored.state.content;
  const occupiedAfterMove = [...content.blocks.slice(1), ...content.fixed_events];
  let movedStart, movedEnd;
  for (const window of content.availability) {
    for (let start = Date.parse(window.start); start + duration * 60000 <= Date.parse(window.end); start += 15 * 60000) {
      const end = start + duration * 60000;
      if (new Date(start).toISOString().slice(0, 16) === beforeMove) continue;
      if (occupiedAfterMove.every(item => end <= Date.parse(item.start) || start >= Date.parse(item.end))) {
        movedStart = new Date(start).toISOString().slice(0, 16);
        movedEnd = new Date(end).toISOString().slice(0, 16);
        break;
      }
    }
    if (movedStart) break;
  }
  expect(movedStart).toBeTruthy();
  await page.locator('#block-start').fill(movedStart);
  await page.locator('#block-end').fill(movedEnd);
  const moveResponse = page.waitForResponse(response => response.url().includes('/api/v1/schedules/') && response.request().method() === 'PUT');
  await page.locator('#block-form button').click();
  const move = await moveResponse;
  expect(move.status(), await move.text()).toBe(200);
  await expect(page.locator('#block-start')).toHaveValue(movedStart);
  await page.locator('#undo').click();
  await expect(page.locator('#block-start')).toHaveValue(beforeMove);
  await page.locator('#redo').click();
  await expect(page.locator('#block-start')).toHaveValue(movedStart);
  await page.locator('#block-locked').check();
  const lockResponse = page.waitForResponse(response => response.url().includes('/api/v1/schedules/') && response.request().method() === 'PUT');
  await page.locator('#block-form button').click();
  expect((await lockResponse).status()).toBe(200);
  await expect(page.locator('#block-locked')).toBeChecked();
  await page.locator('#undo').click();
  await expect(page.locator('#block-locked')).not.toBeChecked();
  await page.locator('#redo').click();
  await expect(page.locator('#block-locked')).toBeChecked();
  const originalStart = await page.locator('#block-start').inputValue();
  await page.locator('#block-start').fill(beforeMove);
  await page.locator('#block-end').fill(beforeEnd);
  const rejectedMove = page.waitForResponse(response => response.url().includes('/api/v1/schedules/') && response.request().method() === 'PUT');
  await page.locator('#block-form button').click();
  const rejected = await rejectedMove;
  expect(rejected.status(), await rejected.text()).toBe(409);
  await expect(page.locator('#notice')).toContainText('قفل');
  await page.reload();
  await page.locator('#saved-schedules button').first().click();
  await expect(page.locator('#block-start')).toHaveValue(originalStart);
  await expect(page.locator('#block-locked')).toBeChecked();
  const csvDownload = page.waitForEvent('download');
  await page.locator('#export-csv').click();
  const csv = await csvDownload;
  const csvText = await readFile(await csv.path(), 'utf8');
  expect(csvText).toContain('Mathematics');
  const icsDownload = page.waitForEvent('download');
  await page.locator('#export-ics').click();
  const ics = await icsDownload;
  const icsText = await readFile(await ics.path(), 'utf8');
  expect(icsText).toContain('BEGIN:VCALENDAR');
  expect(icsText).toContain('SUMMARY:University');
  await expect(page.locator('#saved-schedule')).toBeVisible();
  await page.emulateMedia({ media: 'print' });
  await expect(page.locator('#schedule-result')).toBeVisible();
  await expect(page.locator('#schedule-form')).toBeHidden();
  await page.emulateMedia({ media: 'screen' });
});

test('browser recurrence and mobile RTL layout', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await register(page, 'mobile');
  await page.locator('summary', { hasText: 'فعالیت‌های تکراری' }).click();
  const now = new Date();
  const offset = (6 - now.getUTCDay() + 7) % 7 + 7;
  const saturday = new Date(now.getTime() + offset * 86400000).toISOString().slice(0, 10);
  await page.locator('#schedule-date').fill(saturday);
  await page.locator('#recurrence-title').fill('English practice');
  await page.locator('#recurrence-frequency').selectOption('weekdays');
  await page.locator('#recurrence-weekdays input[value="sat"]').check();
  await page.locator('#recurrence-weekdays input[value="mon"]').check();
  await page.locator('#recurrence-start').fill(saturday);
  await page.locator('#recurrence-form button').first().click();
  await expect(page.locator('#recurrences .item', { hasText: 'English practice' })).toBeVisible();
  await page.locator('#recurrences .item', { hasText: 'English practice' }).getByRole('button', { name: 'ساخت نوبت‌های هفته' }).click();
  await expect(page.locator('#tasks .item', { hasText: 'English practice' })).toHaveCount(2);
  await page.locator('#tasks .item', { hasText: 'English practice' }).first().getByRole('button', { name: 'ویرایش' }).click();
  await page.locator('#task-status').selectOption('completed');
  await page.locator('#task-form button').first().click();
  await expect(page.locator('#tasks .item', { hasText: 'English practice' }).first()).toContainText('completed');
  await expect(page.locator('#tasks .item', { hasText: 'English practice' }).last()).toContainText('pending');
  await page.locator('#recurrences .item', { hasText: 'English practice' }).getByRole('button', { name: 'ویرایش' }).click();
  await page.locator('#recurrence-title').fill('English updated');
  await page.locator('#recurrence-form button').first().click();
  await expect(page.locator('#tasks .item', { hasText: 'English practice' })).toHaveCount(2);
  await expect(page.locator('#recurrences .item', { hasText: 'English updated' })).toBeVisible();
  await page.locator('#recurrences .item', { hasText: 'English updated' }).getByRole('button', { name: 'ساخت نوبت‌های هفته' }).click();
  await expect(page.locator('#tasks .item', { hasText: 'English practice' })).toHaveCount(2);
  expect(await page.locator('html').getAttribute('dir')).toBe('rtl');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
