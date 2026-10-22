import { addDays, availabilityForDays, dateInZone, isoToLocal as zonedLocal, localToIso as zonedIso } from './planner_time.mjs';
import { scheduleCsv, scheduleIcs } from './planner_export.mjs';

(() => {
  const $ = id => document.getElementById(id);
  const state = { csrf: '', user: null, tasks: [], events: [], schedules: [], recurrences: [], preferences: null, preview: null, previewInput: null, saved: null };
  const requestId = () => crypto.randomUUID();
  const message = (text, success = false) => { const node = $('notice'); node.textContent = text || ''; node.className = success ? 'notice success' : 'notice'; };
  const localToIso = value => zonedIso(value, state.user.timezone);
  const isoToLocal = value => zonedLocal(value, state.user.timezone);
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
    $('welcome').textContent = `خوش آمدید، ${state.user.email} · ${state.user.timezone}`;
    $('schedule-date').value = dateInZone(state.user.timezone);
    $('recurrence-start').value = dateInZone(state.user.timezone);
    refresh();
  }
  async function refresh() {
    try {
      const [tasks, events, schedules, recurrences, preferences, saved] = await Promise.all([
        api('/api/v1/tasks?limit=100'), api('/api/v1/fixed-events'), api('/api/v1/schedules'),
        api('/api/v1/recurring-activities?limit=100'), api('/api/v1/me/preferences'),
        state.saved ? api(`/api/v1/schedules/${state.saved.id}`) : Promise.resolve(null),
      ]);
      [state.tasks, state.events, state.schedules, state.recurrences] = [tasks, events, schedules, recurrences];
      if (saved) { state.saved = saved; state.preview = previewFromSaved(saved); }
      if (!state.preferences || state.preferences.version !== preferences.version) renderPreferences(preferences);
      state.preferences = preferences;
      render();
    }
    catch (error) { message(error.message); }
  }
  function button(text, className, action) { const node = document.createElement('button'); node.type = 'button'; node.textContent = text; node.className = className; node.onclick = action; return node; }
  function render() {
    renderItems('tasks', state.tasks, 'هنوز کاری ثبت نشده است.', task => {
      const meta = `مدت: ${task.duration_minutes} دقیقه · اولویت: ${task.priority} · وضعیت: ${task.status}`;
      return [meta, task.earliest_start ? `زودترین شروع: ${format(task.earliest_start)}` : '', task.deadline ? `مهلت: ${format(task.deadline)}` : ''].filter(Boolean).join('\n');
    }, editTask, deleteTask);
    renderItems('events', state.events, 'هنوز رویداد ثابتی ثبت نشده است.', event => `از ${format(event.start)} تا ${format(event.end)}`, editEvent, deleteEvent);
    renderScheduleTasks(); renderRecurrences(); renderPreview(); renderSavedSchedules(); renderSavedSchedule();
  }
  function renderPreferences(preferences) {
    $('activity-start').value = preferences.activity_start.slice(0, 5);
    $('activity-end').value = preferences.activity_end.slice(0, 5);
    $('availability-start').value = preferences.activity_start.slice(0, 5);
    $('availability-end').value = preferences.activity_end.slice(0, 5);
    $('chronotype').value = preferences.chronotype;
    $('focus-block').value = preferences.focus_block_minutes;
    $('break-minutes').value = preferences.break_minutes;
    $('flexibility').value = preferences.flexibility;
    $('workload').value = preferences.workload;
    $('deep-work').value = preferences.deep_work_period;
  }
  function recurrenceValues() {
    const current = state.recurrences.find(item => item.id === $('recurrence-id').value);
    return {
      title: $('recurrence-title').value, description: current?.description || '',
      duration_minutes: Number($('recurrence-duration').value), priority: Number($('recurrence-priority').value),
      splittable: current?.splittable || false, preferred_period: current?.preferred_period || 'any',
      frequency: $('recurrence-frequency').value,
      weekdays: $('recurrence-frequency').value === 'weekdays' ? [...$('recurrence-weekdays').querySelectorAll('input:checked')].map(input => input.value) : [],
      start_date: $('recurrence-start').value, end_date: $('recurrence-end').value || null,
      active: $('recurrence-active').checked,
    };
  }
  function resetRecurrence() {
    $('recurrence-form').reset(); $('recurrence-id').value = ''; $('recurrence-version').value = '';
    $('recurrence-start').value = dateInZone(state.user.timezone); toggleWeekdays();
  }
  function editRecurrence(item) {
    $('recurrence-id').value = item.id; $('recurrence-version').value = item.version;
    $('recurrence-title').value = item.title; $('recurrence-duration').value = item.duration_minutes;
    $('recurrence-priority').value = item.priority; $('recurrence-frequency').value = item.frequency;
    $('recurrence-start').value = item.start_date; $('recurrence-end').value = item.end_date || '';
    $('recurrence-active').checked = item.active;
    $('recurrence-weekdays').querySelectorAll('input').forEach(input => { input.checked = item.weekdays.includes(input.value); });
    toggleWeekdays(); $('recurrence-title').focus();
  }
  function toggleWeekdays() { $('recurrence-weekdays').classList.toggle('hidden', $('recurrence-frequency').value !== 'weekdays'); }
  function renderRecurrences() {
    const host = $('recurrences'); host.textContent = '';
    if (!state.recurrences.length) { host.textContent = 'هنوز فعالیت تکراری ندارید.'; return; }
    state.recurrences.forEach(item => {
      const row = document.createElement('article'); row.className = 'item';
      const title = document.createElement('h3'); title.textContent = `${item.title} · ${item.active ? 'فعال' : 'متوقف'}`;
      const meta = document.createElement('p'); meta.className = 'muted';
      meta.textContent = `${item.duration_minutes} دقیقه · ${item.frequency === 'daily' ? 'هر روز' : item.frequency === 'weekly' ? 'هفتگی' : item.weekdays.join('، ')}`;
      const actions = document.createElement('div'); actions.className = 'actions';
      actions.append(button('ویرایش', 'quiet', () => editRecurrence(item)),
        button('ساخت نوبت‌های هفته', '', () => materialize(item)),
        button(item.active ? 'توقف' : 'فعال‌سازی', 'quiet', () => setRecurrenceActive(item, !item.active)),
        button('حذف الگو', 'danger', () => deleteRecurrence(item)));
      row.append(title, meta, actions); host.append(row);
    });
  }
  async function materialize(item) {
    try {
      const result = await api(`/api/v1/recurring-activities/${item.id}/occurrences`, {
        method: 'POST', body: JSON.stringify({ expected_version: item.version, start_date: $('schedule-date').value, days: 7 }),
      });
      await refresh();
      const generated = new Set(result.tasks.map(task => task.id));
      $('schedule-tasks').querySelectorAll('input').forEach(input => { if (generated.has(input.value)) input.checked = true; });
      message(`${result.created_count} نوبت تازه ساخته شد. نوبت‌های موجود دوباره ساخته نشدند.`, true);
    } catch (error) { message(error.message); }
  }
  async function setRecurrenceActive(item, active) {
    try {
      const { id, user_id, timezone, version, created_at, updated_at, client_request_id, ...values } = item;
      await api(`/api/v1/recurring-activities/${id}`, { method: 'PUT', body: JSON.stringify({ expected_version: version, values: { ...values, active } }) });
      await refresh(); message(active ? 'الگو فعال شد.' : 'الگو متوقف شد؛ نوبت‌های موجود باقی می‌مانند.', true);
    } catch (error) { message(error.message); }
  }
  async function deleteRecurrence(item) {
    if (!confirm(`الگوی «${item.title}» حذف شود؟ نوبت‌های ساخته‌شده باقی می‌مانند.`)) return;
    try { await api(`/api/v1/recurring-activities/${item.id}?expected_version=${item.version}`, { method: 'DELETE' }); await refresh(); message('الگو حذف شد.', true); }
    catch (error) { message(error.message); }
  }
  function renderScheduleTasks() {
    const host = $('schedule-tasks'); const initial = host.dataset.initialized !== 'true';
    const selected = new Set([...host.querySelectorAll('input:checked')].map(node => node.value)); host.textContent = '';
    const pending = state.tasks.filter(task => task.status === 'pending');
    if (!pending.length) { host.textContent = 'کار بازی برای برنامه‌ریزی وجود ندارد.'; return; }
    pending.forEach(task => { const label = document.createElement('label'); const input = document.createElement('input'); input.type = 'checkbox'; input.value = task.id; input.checked = initial || selected.has(task.id); label.append(input, document.createTextNode(`${task.title} (${task.duration_minutes} دقیقه، اولویت ${task.priority})`)); host.append(label); });
    host.dataset.initialized = 'true';
  }
  function renderPreview() {
    const host = $('schedule-result'); $('save-schedule-form').classList.add('hidden'); host.textContent = ''; const preview = state.preview; if (!preview) return;
    const titleFor = id => state.tasks.find(task => task.id === id)?.title || 'کار حذف‌شده';
    const entries = [...preview.fixed_events.map(event => ({ title: event.title, text: `رویداد ثابت · ${format(event.start)} تا ${format(event.end)}`, start: event.start, fixed: true })), ...preview.blocks.map(block => ({ title: titleFor(block.task_id), text: `${format(block.start)} تا ${format(block.end)}${block.locked ? ' · قفل‌شده' : ''}`, start: block.start, fixed: false }))].sort((left, right) => left.start.localeCompare(right.start));
    const heading = document.createElement('h3'); heading.textContent = 'پیش‌نمایش برنامه'; host.append(heading);
    const timeline = document.createElement('div'); timeline.className = 'timeline';
    if (!entries.length) timeline.textContent = 'هیچ بلوکی در وقت آزاد انتخاب‌شده ساخته نشد.';
    const days = state.saved?.state.content.days || state.previewInput?.days || 1;
    const startDate = state.saved?.state.content.start_date || state.previewInput?.start_date;
    for (let offset = 0; offset < days; offset++) {
      const day = addDays(startDate, offset);
      if (days === 7) { const label = document.createElement('h4'); label.textContent = day; timeline.append(label); }
      const onDay = entries.filter(entry => dateInZone(state.user.timezone, new Date(entry.start)) === day);
      if (!onDay.length && days === 7) { const empty = document.createElement('p'); empty.className = 'muted'; empty.textContent = 'زمانی تخصیص نیافته است.'; timeline.append(empty); }
      onDay.forEach(entry => { const block = document.createElement('article'); block.className = entry.fixed ? 'block fixed' : 'block'; const name = document.createElement('h3'); name.textContent = entry.title; const detail = document.createElement('p'); detail.textContent = entry.text; block.append(name, detail); timeline.append(block); });
    }
    host.append(timeline);
    if (preview.unscheduled.length) { const missing = document.createElement('div'); missing.className = 'unscheduled'; const title = document.createElement('h3'); title.textContent = 'کارهای جا‌نشده'; missing.append(title); preview.unscheduled.forEach(item => { const row = document.createElement('p'); row.textContent = `${item.title}: ${item.remaining_minutes} دقیقه — ${item.message}`; missing.append(row); }); host.append(missing); }
    if (preview.warnings.length) { const warnings = document.createElement('p'); warnings.className = 'muted'; warnings.textContent = preview.warnings.join(' '); host.append(warnings); }
    if (!state.saved && state.previewInput) { $('save-schedule-form').classList.remove('hidden'); $('schedule-title').value = `برنامهٔ ${state.previewInput.start_date}`; }
  }
  function previewFromSaved(saved) {
    return { fixed_events: saved.state.content.fixed_events, blocks: saved.state.content.blocks, unscheduled: saved.state.unscheduled, warnings: saved.state.warnings };
  }
  function applySchedule(result) {
    state.saved = result.schedule; state.preview = previewFromSaved(result.schedule); $('save-schedule-form').classList.add('hidden');
    renderPreview(); renderSavedSchedule(); refresh();
  }
  function renderSavedSchedules() {
    const host = $('saved-schedules'); host.textContent = '';
    if (!state.schedules.length) { host.textContent = 'هنوز برنامه‌ای ذخیره نشده است.'; return; }
    state.schedules.forEach(schedule => { const row = document.createElement('article'); row.className = 'item'; const title = document.createElement('h3'); title.textContent = schedule.title; const detail = document.createElement('p'); detail.className = 'muted'; detail.textContent = `آخرین تغییر: ${format(schedule.updated_at)}`; row.append(title, detail, button('بازکردن', 'quiet', () => loadSchedule(schedule.id))); host.append(row); });
  }
  function renderSavedSchedule() {
    const section = $('saved-schedule'); const saved = state.saved; if (!saved) { section.classList.add('hidden'); return; }
    section.classList.remove('hidden'); $('saved-heading').textContent = `${saved.state.title} · نسخهٔ ${saved.version}`;
    $('saved-status').textContent = saved.sources.stale ? 'منابع برنامه تغییر کرده‌اند؛ پیش از تغییر یا برگشت، برنامهٔ تازه بسازید.' : 'برنامه با منابع فعلی هماهنگ است.';
    $('saved-status').className = saved.sources.stale ? 'source-stale' : 'muted'; $('undo').disabled = !saved.can_undo; $('redo').disabled = !saved.can_redo;
    const select = $('saved-block'); const prior = select.value; select.textContent = '';
    saved.state.content.blocks.forEach((block, index) => { const option = document.createElement('option'); option.value = index; option.textContent = `${state.tasks.find(task => task.id === block.task_id)?.title || 'کار'} · ${format(block.start)}`; select.append(option); });
    if (saved.state.content.blocks.length) { $('block-form').classList.remove('hidden'); select.value = prior || '0'; loadBlockIntoForm(); } else { $('block-form').classList.add('hidden'); }
    loadHistory();
  }
  function loadBlockIntoForm() { const block = state.saved?.state.content.blocks[Number($('saved-block').value)]; if (!block) return; $('block-start').value = isoToLocal(block.start); $('block-end').value = isoToLocal(block.end); $('block-locked').checked = block.locked; }
  async function loadSchedule(id) { try { state.saved = await api(`/api/v1/schedules/${id}`); state.preview = previewFromSaved(state.saved); renderPreview(); renderSavedSchedule(); message('برنامهٔ ذخیره‌شده باز شد.', true); } catch (error) { message(error.message); } }
  async function loadHistory() { if (!state.saved) return; try { const entries = await api(`/api/v1/schedules/${state.saved.id}/history`); const host = $('history'); host.textContent = ''; entries.forEach(entry => { const row = document.createElement('p'); row.textContent = `نسخهٔ تاریخی ${entry.revision}: ${entry.title}${entry.current ? ' (فعلی)' : ''}`; host.append(row); }); } catch (error) { message(error.message); } }
  function savedContent() { return JSON.parse(JSON.stringify(state.saved.state.content)); }
  function updatePayload(content) { return { client_request_id: requestId(), expected_version: state.saved.version, title: state.saved.state.title, content, task_versions: state.saved.state.task_versions, preference_version: state.saved.state.preference_version }; }
  function downloadSchedule(kind) {
    if (!state.saved) return;
    const body = kind === 'ics' ? scheduleIcs(state.saved) : scheduleCsv(state.saved);
    const mime = kind === 'ics' ? 'text/calendar;charset=utf-8' : 'text/csv;charset=utf-8';
    const url = URL.createObjectURL(new Blob([body], { type: mime }));
    const link = document.createElement('a'); link.href = url; link.download = `smartplanner-${state.saved.state.content.start_date}.${kind}`;
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function travel(direction) { try { applySchedule(await api(`/api/v1/schedules/${state.saved.id}/${direction}`, { method: 'POST', body: JSON.stringify({ client_request_id: requestId(), expected_version: state.saved.version }) })); message(direction === 'undo' ? 'تغییر برگشت داده شد.' : 'تغییر دوباره اعمال شد.', true); } catch (error) { message(error.message); } }
  function renderItems(id, data, empty, detail, edit, remove) {
    const host = $(id); host.textContent = '';
    if (!data.length) { const node = document.createElement('p'); node.className = 'muted'; node.textContent = empty; host.append(node); return; }
    data.forEach(item => { const box = document.createElement('article'); box.className = 'item'; const heading = document.createElement('h3'); heading.textContent = item.title; const text = document.createElement('p'); text.textContent = item.description || detail(item); if (item.description) { const meta = document.createElement('p'); meta.className = 'muted'; meta.textContent = detail(item); box.append(heading, text, meta); } else box.append(heading, text); const actions = document.createElement('div'); actions.className = 'actions'; actions.append(button('ویرایش', 'quiet', () => edit(item)), button('حذف', 'danger', () => remove(item))); box.append(actions); host.append(box); });
  }
  function resetTask() { $('task-form').reset(); $('task-id').value = ''; $('task-version').value = ''; $('task-status').value = 'pending'; $('task-heading').textContent = 'افزودن کار'; }
  function resetEvent() { $('event-form').reset(); $('event-id').value = ''; $('event-version').value = ''; $('event-heading').textContent = 'افزودن رویداد ثابت'; }
  function editTask(task) { $('task-id').value = task.id; $('task-version').value = task.version; $('task-title').value = task.title; $('task-description').value = task.description; $('task-duration').value = task.duration_minutes; $('task-priority').value = task.priority; $('task-earliest').value = isoToLocal(task.earliest_start); $('task-deadline').value = isoToLocal(task.deadline); $('task-period').value = task.preferred_period; $('task-status').value = task.status; $('task-splittable').checked = task.splittable; $('task-heading').textContent = 'ویرایش کار'; $('task-title').focus(); }
  function editEvent(event) { $('event-id').value = event.id; $('event-version').value = event.version; $('event-title').value = event.title; $('event-start').value = isoToLocal(event.start); $('event-end').value = isoToLocal(event.end); $('event-heading').textContent = 'ویرایش رویداد ثابت'; $('event-title').focus(); }
  async function deleteTask(task) { if (!confirm(`«${task.title}» حذف شود؟`)) return; try { await api(`/api/v1/tasks/${task.id}?expected_version=${task.version}`, { method: 'DELETE' }); message('کار حذف شد.', true); refresh(); } catch (error) { message(error.message); } }
  async function deleteEvent(event) { if (!confirm(`«${event.title}» حذف شود؟`)) return; try { await api(`/api/v1/fixed-events/${event.id}?expected_version=${event.version}`, { method: 'DELETE' }); message('رویداد حذف شد.', true); refresh(); } catch (error) { message(error.message); } }
  $('login-form').onsubmit = async event => { event.preventDefault(); try { showPlanner(await api('/api/v1/auth/login', { method: 'POST', body: JSON.stringify({ email: $('login-email').value, password: $('login-password').value }) })); } catch (error) { message(error.message); } };
  $('register-form').onsubmit = async event => { event.preventDefault(); try { const email = $('register-email').value; const password = $('register-password').value; await api('/api/v1/auth/register', { method: 'POST', body: JSON.stringify({ email, password, timezone: $('timezone').value }) }); showPlanner(await api('/api/v1/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) })); } catch (error) { message(error.message); } };
  $('preferences-form').onsubmit = async event => {
    event.preventDefault();
    const values = { chronotype: $('chronotype').value, focus_block_minutes: Number($('focus-block').value),
      break_minutes: Number($('break-minutes').value), flexibility: $('flexibility').value,
      workload: $('workload').value, deep_work_period: $('deep-work').value,
      activity_start: $('activity-start').value, activity_end: $('activity-end').value };
    try {
      state.preferences = await api('/api/v1/me/preferences', { method: 'PUT', body: JSON.stringify({ expected_version: state.preferences.version, values }) });
      state.preview = null; state.previewInput = null; renderPreview();
      if (state.saved) await loadSchedule(state.saved.id);
      message('ترجیحات ذخیره شد. پیش‌نمایش بعدی با تنظیمات تازه ساخته می‌شود.', true);
    } catch (error) { message(error.message); }
  };
  $('recurrence-form').onsubmit = async event => {
    event.preventDefault(); const id = $('recurrence-id').value; const values = recurrenceValues();
    if (values.frequency === 'weekdays' && !values.weekdays.length) { message('دست‌کم یک روز را انتخاب کنید.'); return; }
    try {
      await api(id ? `/api/v1/recurring-activities/${id}` : '/api/v1/recurring-activities', {
        method: id ? 'PUT' : 'POST', body: JSON.stringify(id
          ? { expected_version: Number($('recurrence-version').value), values }
          : { client_request_id: requestId(), ...values }),
      });
      resetRecurrence(); await refresh(); message('الگوی تکرار ذخیره شد.', true);
    } catch (error) { message(error.message); }
  };
  $('task-form').onsubmit = async event => { event.preventDefault(); const id = $('task-id').value; try { const data = { title: $('task-title').value, description: $('task-description').value, duration_minutes: Number($('task-duration').value), priority: Number($('task-priority').value), earliest_start: localToIso($('task-earliest').value), deadline: localToIso($('task-deadline').value), preferred_period: $('task-period').value, splittable: $('task-splittable').checked }; if (!id) data.client_request_id = requestId(); else { data.expected_version = Number($('task-version').value); data.status = $('task-status').value; } await api(id ? `/api/v1/tasks/${id}` : '/api/v1/tasks', { method: id ? 'PATCH' : 'POST', body: JSON.stringify(data) }); resetTask(); message('کار ذخیره شد.', true); refresh(); } catch (error) { message(error.message); } };
  $('event-form').onsubmit = async event => { event.preventDefault(); const id = $('event-id').value; try { const data = { title: $('event-title').value, start: localToIso($('event-start').value), end: localToIso($('event-end').value) }; if (id) data.expected_version = Number($('event-version').value); await api(id ? `/api/v1/fixed-events/${id}` : '/api/v1/fixed-events', { method: id ? 'PUT' : 'POST', body: JSON.stringify(data) }); resetEvent(); message('رویداد ذخیره شد.', true); refresh(); } catch (error) { message(error.message); } };
  $('schedule-form').onsubmit = async event => {
    event.preventDefault();
    const selected = [...$('schedule-tasks').querySelectorAll('input:checked')].map(node => node.value);
    if (!selected.length) { message('دست‌کم یک کار را انتخاب کنید.'); return; }
    try {
      const date = $('schedule-date').value; const days = Number($('schedule-days').value);
      const availability = availabilityForDays(date, days, $('availability-start').value, $('availability-end').value, state.user.timezone);
      const oldContent = state.saved?.state.content;
      const sameHorizon = oldContent?.start_date === date && oldContent?.days === days;
      const previous = (sameHorizon ? oldContent.blocks : []).filter(block => selected.includes(block.task_id));
      state.previewInput = { start_date: date, days, task_ids: selected, availability, previous_blocks: previous };
      state.preview = await api('/api/v1/schedules/preview', { method: 'POST', body: JSON.stringify(state.previewInput) });
      state.saved = null; renderPreview(); renderSavedSchedule();
      message('پیش‌نمایش ساخته شد؛ کارهای جا‌نشده همراه دلیل نمایش داده می‌شوند.', true);
    } catch (error) { state.preview = null; renderPreview(); message(error.message); }
  };
  $('save-schedule-form').onsubmit = async event => { event.preventDefault(); if (!state.preview || !state.previewInput) return; const content = { ...state.previewInput, fixed_events: state.preview.fixed_events, blocks: state.preview.blocks }; delete content.previous_blocks; try { applySchedule(await api('/api/v1/schedules', { method: 'POST', body: JSON.stringify({ client_request_id: requestId(), title: $('schedule-title').value, content, task_versions: state.preview.task_versions, preference_version: state.preview.preference_version }) })); message('برنامه ذخیره شد.', true); } catch (error) { message(error.message); } };
  $('block-form').onsubmit = async event => { event.preventDefault(); if (!state.saved) return; try { const content = savedContent(); const index = Number($('saved-block').value); const old = content.blocks[index]; const start = localToIso($('block-start').value); const end = localToIso($('block-end').value); const changedTime = old.start !== start || old.end !== end; const changedLock = old.locked !== $('block-locked').checked; if (changedTime && changedLock) { message('قفل را در یک تغییر جدا باز یا بسته کنید، سپس زمان را تغییر دهید.'); return; } old.start = start; old.end = end; old.locked = $('block-locked').checked; applySchedule(await api(`/api/v1/schedules/${state.saved.id}`, { method: 'PUT', body: JSON.stringify(updatePayload(content)) })); message('بلوک با حفظ سایر بلوک‌ها تغییر کرد.', true); } catch (error) { message(error.message); } };
  $('task-cancel').onclick = resetTask; $('event-cancel').onclick = resetEvent;
  $('recurrence-cancel').onclick = resetRecurrence; $('recurrence-frequency').onchange = toggleWeekdays;
  $('export-ics').onclick = () => downloadSchedule('ics'); $('export-csv').onclick = () => downloadSchedule('csv');
  $('export-pdf').onclick = () => { if (state.saved) window.print(); };
  $('saved-block').onchange = loadBlockIntoForm; $('undo').onclick = () => travel('undo'); $('redo').onclick = () => travel('redo');
  $('logout').onclick = async () => { try { await api('/api/v1/auth/logout', { method: 'POST' }); location.reload(); } catch (error) { message(error.message); } };
  api('/api/v1/auth/session').then(showPlanner).catch(() => {});
})();
