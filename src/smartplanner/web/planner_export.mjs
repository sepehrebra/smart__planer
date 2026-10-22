const utcStamp = value => new Date(value).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
const icsText = value => String(value).replace(/\\/g, '\\\\').replace(/\n/g, '\\n').replace(/,/g, '\\,').replace(/;/g, '\\;');
const csvCell = value => {
  const text = String(value ?? '');
  // Prevent spreadsheet applications from interpreting user titles as formulas.
  const safe = /^[\s]*[=+@-]/.test(text) ? `'${text}` : text;
  return `"${safe.replace(/"/g, '""')}"`;
};
function foldIcs(line) {
  const pieces = []; let current = '';
  for (const character of line) {
    if (new TextEncoder().encode(current + character).length > 75) {
      pieces.push(current); current = ' ' + character;
    } else current += character;
  }
  pieces.push(current);
  return pieces.join('\r\n');
}

export function scheduleRows(schedule) {
  const content = schedule.state.content;
  return [
    ...content.fixed_events.map((event, index) => ({ id: `fixed-${index}`, type: 'fixed', title: event.title, start: event.start, end: event.end })),
    ...content.blocks.map((block, index) => ({ id: `task-${block.task_id}-${index}`, type: 'task',
      title: schedule.state.task_titles[block.task_id] || 'Task', start: block.start, end: block.end })),
  ].sort((a, b) => a.start.localeCompare(b.start));
}

export function scheduleCsv(schedule) {
  const lines = [['type', 'title', 'start_utc', 'end_utc'],
    ...scheduleRows(schedule).map(row => [row.type, row.title, row.start, row.end])];
  return '\uFEFF' + lines.map(row => row.map(csvCell).join(',')).join('\r\n') + '\r\n';
}

export function scheduleIcs(schedule) {
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//SmartPlanner//Schedule//EN', 'CALSCALE:GREGORIAN'];
  for (const row of scheduleRows(schedule)) {
    lines.push('BEGIN:VEVENT', `UID:${schedule.id}-${row.id}@smartplanner`,
      `DTSTAMP:${utcStamp(schedule.updated_at)}`, `DTSTART:${utcStamp(row.start)}`,
      `DTEND:${utcStamp(row.end)}`, `SUMMARY:${icsText(row.title)}`, 'END:VEVENT');
  }
  lines.push('END:VCALENDAR');
  return lines.map(foldIcs).join('\r\n') + '\r\n';
}
