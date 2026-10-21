// Convert wall-clock form values in the account's timezone, independent of device timezone.
const formatter = zone => new Intl.DateTimeFormat('en-US-u-ca-gregory', {
  timeZone: zone, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit',
});

function parts(instant, zone) {
  return Object.fromEntries(formatter(zone).formatToParts(instant)
    .filter(part => part.type !== 'literal').map(part => [part.type, part.value]));
}

export function dateInZone(zone, instant = new Date()) {
  const value = parts(instant, zone);
  return `${value.year}-${value.month}-${value.day}`;
}

export function isoToLocal(value, zone) {
  if (!value) return '';
  const fields = parts(new Date(value), zone);
  return `${fields.year}-${fields.month}-${fields.day}T${fields.hour}:${fields.minute}`;
}

export function localToIso(value, zone) {
  if (!value) return null;
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) throw new RangeError('زمان واردشده معتبر نیست.');
  const [year, month, day, hour, minute] = value.match(/\d+/g).map(Number);
  const wall = Date.UTC(year, month - 1, day, hour, minute);
  let instant = wall;
  for (let attempt = 0; attempt < 5; attempt++) {
    const local = parts(new Date(instant), zone);
    const shown = Date.UTC(+local.year, +local.month - 1, +local.day, +local.hour, +local.minute);
    const next = instant + wall - shown;
    if (next === instant) break;
    instant = next;
  }
  if (isoToLocal(new Date(instant).toISOString(), zone) !== value) {
    throw new RangeError('این ساعت در منطقهٔ زمانی حساب وجود ندارد؛ ساعت دیگری انتخاب کنید.');
  }
  return new Date(instant).toISOString();
}

export function addDays(date, days) {
  const [year, month, day] = date.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
}

export function availabilityForDays(startDate, days, startClock, endClock, zone) {
  if (startClock >= endClock) throw new RangeError('پایان وقت آزاد باید بعد از شروع آن باشد.');
  return Array.from({ length: days }, (_, offset) => {
    const day = addDays(startDate, offset);
    return { start: localToIso(`${day}T${startClock}`, zone), end: localToIso(`${day}T${endClock}`, zone) };
  });
}
