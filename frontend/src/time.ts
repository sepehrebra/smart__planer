import { Temporal } from "@js-temporal/polyfill";
export const number = (n: number) => new Intl.NumberFormat("fa-IR").format(n);
export const today = (zone: string) =>
  Temporal.Now.plainDateISO(zone).toString();
export const addDays = (date: string, n: number) =>
  Temporal.PlainDate.from(date).add({ days: n }).toString();
export function instant(local: string, zone: string) {
  try {
    return Temporal.PlainDateTime.from(local)
      .toZonedDateTime(zone, { disambiguation: "reject" })
      .toInstant()
      .toString();
  } catch {
    throw new Error(
      "این ساعت در منطقهٔ زمانی حساب معتبر یا یکتا نیست؛ ساعت دیگری انتخاب کنید.",
    );
  }
}
export function localInput(value: string, zone: string) {
  return Temporal.Instant.from(value)
    .toZonedDateTimeISO(zone)
    .toPlainDateTime()
    .toString({ smallestUnit: "minute" });
}
export const dateOf = (value: string, zone: string) =>
  localInput(value, zone).slice(0, 10);
export const clock = (value: string, zone: string) =>
  new Intl.DateTimeFormat("fa-IR", {
    timeZone: zone,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
export const dateLabel = (date: string) =>
  new Intl.DateTimeFormat("fa-IR", {
    weekday: "long",
    day: "numeric",
    month: "long",
    timeZone: "UTC",
  }).format(new Date(date + "T12:00:00Z"));
export const duration = (start: string, end: string) =>
  Math.round((Date.parse(end) - Date.parse(start)) / 60000);

export const midnight = (date: string, zone: string) =>
  Temporal.PlainDate.from(date).toZonedDateTime(zone).toInstant().toString();
