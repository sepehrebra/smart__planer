import { test, expect } from 'playwright/test';
import { request } from 'playwright';
import { randomUUID } from 'node:crypto';

test('five-user SQLite API load smoke', async () => {
  const clients = await Promise.all(Array.from({ length: 5 }, () => request.newContext({ baseURL: 'http://localhost:8765' })));
  const samples = []; const errors = [];
  const day = new Date(Date.now() + 3 * 86400000).toISOString().slice(0, 10);
  async function measured(label, action) {
    const started = performance.now();
    const response = await action();
    samples.push({ label, ms: Math.round(performance.now() - started), status: response.status() });
    if (response.status() >= 400) errors.push({ label, status: response.status(), code: (await response.json()).code });
    return response;
  }
  try {
    const sessions = [];
    for (const [index, client] of clients.entries()) {
      const email = `load-${index}-${randomUUID()}@example.com`;
      await measured('register', () => client.post('/api/v1/auth/register', { data: { email, password: 'load-test-password-2026', timezone: 'UTC' } }));
      const login = await measured('login', () => client.post('/api/v1/auth/login', { data: { email, password: 'load-test-password-2026' } }));
      if (login.status() === 200) sessions.push({ client, index, csrf: (await login.json()).csrf_token });
    }
    await Promise.all(sessions.map(async ({ client, index, csrf }) => {
      const headers = { 'X-CSRF-Token': csrf };
      const task = await measured('task', () => client.post('/api/v1/tasks', { headers, data: { client_request_id: randomUUID(), title: `Load task ${index}`, duration_minutes: 30 } }));
      if (task.status() !== 201) return;
      const taskId = (await task.json()).id;
      await measured('preview', () => client.post('/api/v1/schedules/preview', { headers, data: {
        start_date: day, days: 1, task_ids: [taskId],
        availability: [{ start: `${day}T09:00:00Z`, end: `${day}T18:00:00Z` }],
      } }));
    }));
    const ordered = samples.map(sample => sample.ms).sort((a, b) => a - b);
    const p95 = ordered[Math.ceil(ordered.length * 0.95) - 1];
    console.log(JSON.stringify({ users: 5, requests: samples.length, errors: errors.length, p50_ms: ordered[Math.floor(ordered.length / 2)], p95_ms: p95,
      preview_ms: samples.filter(sample => sample.label === 'preview').map(sample => sample.ms) }));
    expect(samples).toHaveLength(20);
    // The server intentionally admits at most two concurrent planning requests.
    // Measure rejected work while rejecting unexpected errors or data loss.
    expect(errors.every(error => error.label === 'preview' && error.status === 429 && error.code === 'planner_busy')).toBe(true);
    for (const { client } of sessions) expect((await client.get('/api/v1/tasks')).status()).toBe(200);
  } finally {
    await Promise.all(clients.map(client => client.dispose()));
  }
});
