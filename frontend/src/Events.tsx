import { useState, type FormEvent } from "react";
import { CalendarPlus, Pencil, Trash2, LockKeyhole } from "lucide-react";
import { api, ApiError, message } from "./api";
import { Field, Modal, ErrorBox, Empty } from "./ui";
import { clock, dateOf, dateLabel, instant, localInput, today } from "./time";
import type { FixedEvent } from "./types";
export function Events({
  events,
  zone,
  onChanged,
}: {
  events: FixedEvent[];
  zone: string;
  onChanged: () => Promise<void>;
}) {
  const [editing, setEditing] = useState<FixedEvent | "new" | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function remove(event: FixedEvent) {
    if (!confirm(`«${event.title}» حذف شود؟`)) return;
    setBusy(true);
    try {
      await api(
        `/fixed-events/${event.id}?expected_version=${event.version}`,
        "DELETE",
      );
      await onChanged();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="section-head">
        <div>
          <span className="eyebrow">زمان‌هایی که از قبل رزرو شده‌اند</span>
          <h1>کلاس‌ها و جلسه‌ها</h1>
          <p className="muted">
            برنامه‌ریز برای این زمان‌ها کار دیگری نمی‌چیند.
          </p>
        </div>
        <button className="primary" onClick={() => setEditing("new")}>
          <CalendarPlus size={18} />
          تعهد جدید
        </button>
      </div>
      <ErrorBox text={error} />
      <section className="panel">
        {events.length ? (
          <div className="event-grid">
            {events.map((event) => (
              <article className="event-card" key={event.id}>
                <div className="event-icon">
                  <LockKeyhole size={21} />
                </div>
                <span className="badge">زمان ثابت</span>
                <h3>{event.title}</h3>
                <p>{dateLabel(dateOf(event.start, zone))}</p>
                <strong className="time-label">
                  {clock(event.start, zone)} — {clock(event.end, zone)}
                </strong>
                {dateOf(event.start, zone) !== dateOf(event.end, zone) && (
                  <small>تا {dateLabel(dateOf(event.end, zone))}</small>
                )}
                <div className="event-actions">
                  <button
                    className="text-button"
                    onClick={() => setEditing(event)}
                  >
                    <Pencil size={15} />
                    ویرایش
                  </button>
                  <button
                    className="text-button danger"
                    disabled={busy}
                    onClick={() => remove(event)}
                  >
                    <Trash2 size={15} />
                    حذف
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <Empty
            title="برای زمان‌های ثابتت جا نگه دار"
            detail="کلاس دانشگاه، جلسهٔ کاری یا یک قرار مهم را اینجا ثبت کن."
            action={
              <button className="secondary" onClick={() => setEditing("new")}>
                ثبت اولین تعهد
              </button>
            }
          />
        )}
      </section>
      {editing && (
        <EventEditor
          event={editing === "new" ? null : editing}
          zone={zone}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await onChanged();
          }}
        />
      )}
    </>
  );
}
function EventEditor({
  event,
  zone,
  onClose,
  onSaved,
}: {
  event: FixedEvent | null;
  zone: string;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [uncertain, setUncertain] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const f = new FormData(e.currentTarget);
    try {
      const body = {
        title: String(f.get("title")),
        start: instant(String(f.get("start")), zone),
        end: instant(String(f.get("end")), zone),
      };
      if (body.end <= body.start)
        throw new Error("پایان باید بعد از شروع باشد.");
      await api(
        event ? `/fixed-events/${event.id}` : "/fixed-events",
        event ? "PUT" : "POST",
        event ? { ...body, expected_version: event.version } : body,
      );
      await onSaved();
    } catch (e) {
      setError(message(e));
      if (e instanceof ApiError && e.status === 0 && !event) setUncertain(true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={event ? "ویرایش تعهد" : "ثبت کلاس یا جلسه"}
      onClose={() => !busy && onClose()}
    >
      <form onSubmit={submit}>
        <fieldset disabled={busy || uncertain}>
          <Field label="عنوان تعهد">
            <input
              name="title"
              defaultValue={event?.title}
              placeholder="مثلاً کلاس محاسبات علمی"
              maxLength={200}
              required
              autoFocus
            />
          </Field>
          <Field label="شروع" hint={`به وقت ${zone}؛ ورود تاریخ میلادی است.`}>
            <input
              type="datetime-local"
              name="start"
              required
              defaultValue={
                event ? localInput(event.start, zone) : today(zone) + "T10:00"
              }
            />
          </Field>
          <Field label="پایان">
            <input
              type="datetime-local"
              name="end"
              required
              defaultValue={
                event ? localInput(event.end, zone) : today(zone) + "T12:00"
              }
            />
          </Field>
          <ErrorBox text={error} />
          <div className="modal-actions">
            <button className="primary" type="submit">
              {busy ? "در حال ذخیره…" : "ذخیرهٔ تعهد"}
            </button>
            <button type="button" className="secondary" onClick={onClose}>
              انصراف
            </button>
          </div>
        </fieldset>
        {uncertain && (
          <div className="notice warning">
            <p>
              برای جلوگیری از ثبت تکراری، ابتدا فهرست را تازه کنید و وجود این
              تعهد را بررسی کنید.
            </p>
            <button type="button" className="secondary" onClick={onSaved}>
              بررسی فهرست تعهدها
            </button>
          </div>
        )}
      </form>
    </Modal>
  );
}
