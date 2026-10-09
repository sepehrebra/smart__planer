(() => {
  const $ = id => document.getElementById(id);
  const state = { csrf: '', user: null, tasks: [], events: [], preview: null };
  const requestId = () => crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  const message = (text, success = false) => { const node = $('notice'); node.textContent = text || ''; node.className = success ? 'notice success' : 'notice'; };
  const localToIso = value => value ? new Date(value).toISOString() : null;
  const isoToLocal = value => value ? new Date(value).toISOString().slice(0, 16) : '';
  const format = value => value ? new Intl.DateTimeFormat('fa-IR', { dateStyle: 'short', timeStyle: 'short', timeZone: state.user.timezone }).format(new Date(value)) : '—';
  async function api(path, options = {}) {
    const headers = { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) };
    if (state.csrf && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(options.method)) headers['X-CSRF-Token'] = state.csrf;
    const response = await fetch(path, { ...options, headers, credentials: 'same-origin' });
    if (response.status === 204) return null;
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.message || 'عملیات انجام نشد.');
    return body;
  }
  function showPlanner(session) {
    state.user = session.user; state.csrf = session.csrf_token;
    $('auth').classList.add('hidden'); $('planner').classList.remove('hidden'); $('logout').classList.remove('hidden');
    $('welcome').textContent = `خوش آمدید، ${state.user.email}`; refresh();
  }
  async function refresh() {
    try { [state.tasks, state.events] = await Promise.all([api('/api/v1/tasks'), api('/api/v1/fixed-events')]); render(); }
    catch (error) { message(error.message); }
  }
  function button(text, className, action) { const node = document.createElement('button'); node.type = 'button'; node.textContent = text; node.className = className; node.onclick = action; return node; }
  function render() {
    renderItems('tasks', state.tasks, 'هنوز کاری ثبت نشده است.', task => {
      const meta = `مدت: ${task.duration_minutes} دقیقه · اولویت: ${task.priority} · وضعیت: ${task.status}`;
      return [meta, task.earliest_start ? `زودترین شروع: ${format(task.earliest_start)}` : '', task.deadline ? `مهلت: ${format(task.deadline)}` : ''].filter(Boolean).join('\n');
    }, editTask, deleteTask);
    renderItems('events', state.events, 'هنوز رویداد ثابتی ثبت نشده است.', event => `از ${format(event.start)} تا ${format(event.end)}`, editEvent, deleteEvent);
    renderScheduleTasks(); renderPreview();
  }
  function renderScheduleTasks() {
    const host = $('schedule-tasks'); const selected = new Set([...host.querySelectorAll('input:checked')].map(node => node.value)); host.textContent = '';
    const pending = state.tasks.filter(task => task.status === 'pending');
    if (!pending.length) { host.textContent = 'کار بازی برای برنامه‌ریزی وجود ندارد.'; return; }
    pending.forEach(task => { const label = document.createElement('label'); const input = document.createElement('input'); input.type = 'checkbox'; input.value = task.id; input.checked = selected.size ? selected.has(task.id) : true; label.append(input, document.createTextNode(`${task.title} (${task.duration_minutes} دقیقه، اولویت ${task.priority})`)); host.append(label); });
  }
  function renderPreview() {
    const host = $('schedule-result'); host.textContent = ''; const preview = state.preview; if (!preview) return;
    const titleFor = id => state.tasks.find(task => task.id === id)?.title || 'کار حذف‌شده';
    const entries = [...preview.fixed_events.map(event => ({ title: event.title, text: `رویداد ثابت · ${format(event.start)} تا ${format(event.end)}`, start: event.start, fixed: true })), ...preview.blocks.map(block => ({ title: titleFor(block.task_id), text: `${format(block.start)} تا ${format(block.end)}${block.locked ? ' · قفل‌شده' : ''}`, start: block.start, fixed: false }))].sort((left, right) => left.start.localeCompare(right.start));
    const heading = document.createElement('h3'); heading.textContent = 'پیش‌نمایش برنامه'; host.append(heading);
    const timeline = document.createElement('div'); timeline.className = 'timeline';
    if (!entries.length) timeline.textContent = 'هیچ بلوکی در وقت آزاد انتخاب‌شده ساخته نشد.';
    entries.forEach(entry => { const block = document.createElement('article'); block.className = entry.fixed ? 'block fixed' : 'block'; const name = document.createElement('h3'); name.textContent = entry.title; const detail = document.createElement('p'); detail.textContent = entry.text; block.append(name, detail); timeline.append(block); }); host.append(timeline);
    if (preview.unscheduled.length) { const missing = document.createElement('div'); missing.className = 'unscheduled'; const title = document.createElement('h3'); title.textContent = 'کارهای جا‌نشده'; missing.append(title); preview.unscheduled.forEach(item => { const row = document.createElement('p'); row.textContent = `${item.title}: ${item.remaining_minutes} دقیقه — ${item.message}`; missing.append(row); }); host.append(missing); }
    if (preview.warnings.length) { const warnings = document.createElement('p'); warnings.className = 'muted'; warnings.textContent = preview.warnings.join(' '); host.append(warnings); }
  }
  function renderItems(id, data, empty, detail, edit, remove) {
    const host = $(id); host.textContent = '';
    if (!data.length) { const node = document.createElement('p'); node.className = 'muted'; node.textContent = empty; host.append(node); return; }
    data.forEach(item => { const box = document.createElement('article'); box.className = 'item'; const heading = document.createElement('h3'); heading.textContent = item.title; const text = document.createElement('p'); text.textContent = item.description || detail(item); if (item.description) { const meta = document.createElement('p'); meta.className = 'muted'; meta.textContent = detail(item); box.append(heading, text, meta); } else box.append(heading, text); const actions = document.createElement('div'); actions.className = 'actions'; actions.append(button('ویرایش', 'quiet', () => edit(item)), button('حذف', 'danger', () => remove(item))); box.append(actions); host.append(box); });
  }
  function resetTask() { $('task-form').reset(); $('task-id').value = ''; $('task-version').value = ''; $('task-heading').textContent = 'افزودن کار'; }
  function resetEvent() { $('event-form').reset(); $('event-id').value = ''; $('event-version').value = ''; $('event-heading').textContent = 'افزودن رویداد ثابت'; }
  function editTask(task) { $('task-id').value = task.id; $('task-version').value = task.version; $('task-title').value = task.title; $('task-description').value = task.description; $('task-duration').value = task.duration_minutes; $('task-priority').value = task.priority; $('task-earliest').value = isoToLocal(task.earliest_start); $('task-deadline').value = isoToLocal(task.deadline); $('task-period').value = task.preferred_period; $('task-splittable').checked = task.splittable; $('task-heading').textContent = 'ویرایش کار'; $('task-title').focus(); }
  function editEvent(event) { $('event-id').value = event.id; $('event-version').value = event.version; $('event-title').value = event.title; $('event-start').value = isoToLocal(event.start); $('event-end').value = isoToLocal(event.end); $('event-heading').textContent = 'ویرایش رویداد ثابت'; $('event-title').focus(); }
  async function deleteTask(task) { if (!confirm(`«${task.title}» حذف شود؟`)) return; try { await api(`/api/v1/tasks/${task.id}?expected_version=${task.version}`, { method: 'DELETE' }); message('کار حذف شد.', true); refresh(); } catch (error) { message(error.message); } }
  async function deleteEvent(event) { if (!confirm(`«${event.title}» حذف شود؟`)) return; try { await api(`/api/v1/fixed-events/${event.id}?expected_version=${event.version}`, { method: 'DELETE' }); message('رویداد حذف شد.', true); refresh(); } catch (error) { message(error.message); } }
  $('login-form').onsubmit = async event => { event.preventDefault(); try { showPlanner(await api('/api/v1/auth/login', { method: 'POST', body: JSON.stringify({ email: $('login-email').value, password: $('login-password').value }) })); } catch (error) { message(error.message); } };
  $('register-form').onsubmit = async event => { event.preventDefault(); try { const email = $('register-email').value; const password = $('register-password').value; await api('/api/v1/auth/register', { method: 'POST', body: JSON.stringify({ email, password, timezone: $('timezone').value }) }); showPlanner(await api('/api/v1/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) })); } catch (error) { message(error.message); } };
  $('task-form').onsubmit = async event => { event.preventDefault(); const id = $('task-id').value; const data = { title: $('task-title').value, description: $('task-description').value, duration_minutes: Number($('task-duration').value), priority: Number($('task-priority').value), earliest_start: localToIso($('task-earliest').value), deadline: localToIso($('task-deadline').value), preferred_period: $('task-period').value, splittable: $('task-splittable').checked }; if (!id) data.client_request_id = requestId(); else data.expected_version = Number($('task-version').value); try { await api(id ? `/api/v1/tasks/${id}` : '/api/v1/tasks', { method: id ? 'PATCH' : 'POST', body: JSON.stringify(data) }); resetTask(); message('کار ذخیره شد.', true); refresh(); } catch (error) { message(error.message); } };
  $('event-form').onsubmit = async event => { event.preventDefault(); const id = $('event-id').value; const data = { title: $('event-title').value, start: localToIso($('event-start').value), end: localToIso($('event-end').value) }; if (id) data.expected_version = Number($('event-version').value); try { await api(id ? `/api/v1/fixed-events/${id}` : '/api/v1/fixed-events', { method: id ? 'PUT' : 'POST', body: JSON.stringify(data) }); resetEvent(); message('رویداد ذخیره شد.', true); refresh(); } catch (error) { message(error.message); } };
  $('schedule-form').onsubmit = async event => { event.preventDefault(); const selected = [...$('schedule-tasks').querySelectorAll('input:checked')].map(node => node.value); const date = $('schedule-date').value; const start = localToIso(`${date}T${$('availability-start').value}`); const end = localToIso(`${date}T${$('availability-end').value}`); if (!selected.length) { message('دست‌کم یک کار را انتخاب کنید.'); return; } try { state.preview = await api('/api/v1/schedules/preview', { method: 'POST', body: JSON.stringify({ start_date: date, days: 1, task_ids: selected, availability: [{ start, end }] }) }); renderPreview(); message('پیش‌نمایش برنامه ساخته شد.', true); } catch (error) { state.preview = null; renderPreview(); message(error.message); } };
  $('task-cancel').onclick = resetTask; $('event-cancel').onclick = resetEvent;
  $('logout').onclick = async () => { try { await api('/api/v1/auth/logout', { method: 'POST' }); location.reload(); } catch (error) { message(error.message); } };
  $('schedule-date').value = new Date().toISOString().slice(0, 10);
  api('/api/v1/auth/session').then(showPlanner).catch(() => {});
})();
