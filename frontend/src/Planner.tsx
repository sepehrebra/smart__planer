import { useEffect, useState, type FormEvent } from "react";
import {
  CalendarDays,
  ChevronRight,
  ChevronLeft,
  Plus,
  Sparkles,
  Save,
  Undo2,
  Redo2,
  LockKeyhole,
  LockKeyholeOpen,
  Pencil,
  Clock3,
  CheckCircle2,
  ArrowLeft,
  ListChecks,
} from "lucide-react";
import { api, command, message } from "./api";
import { Field, Modal, ErrorBox, Empty } from "./ui";
import {
  addDays,
  today,
  instant,
  midnight,
  dateOf,
  dateLabel,
  clock,
  duration,
  localInput,
  number,
} from "./time";
import type {
  Task,
  FixedEvent,
  Summary,
  Saved,
  Draft,
  Preview,
  Block,
  TimeWindowTitle,
} from "./types";
interface Props {
  tasks: Task[];
  events: FixedEvent[];
  schedules: Summary[];
  zone: string;
  onChanged: () => Promise<void>;
  onDirty: (v: boolean) => void;
  onTasks: () => void;
}
export function Planner({
  tasks,
  events,
  schedules,
  zone,
  onChanged,
  onDirty,
  onTasks,
}: Props) {
  const [start, setStart] = useState(today(zone)),
    [days, setDays] = useState(1),
    [activeDay, setActiveDay] = useState(today(zone)),
    [from, setFrom] = useState("09:00"),
    [to, setTo] = useState("20:00");
  const [selected, setSelected] = useState<string[]>([]),
    [saved, setSaved] = useState<Saved | null>(null),
    [draft, setDraft] = useState<Draft | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [moving, setMoving] = useState<number | null>(null);
  useEffect(() => {
    onDirty(!!draft);
    return () => onDirty(false);
  }, [draft, onDirty]);
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (draft) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [draft]);
  useEffect(() => {
    if (!saved || draft) return;
    let active = true;
    void api<Saved>(`/schedules/${saved.id}`)
      .then((s) => {
        if (active) setSaved(s);
      })
      .catch((e) => {
        if (active) setError(message(e));
      });
    return () => {
      active = false;
    };
  }, [schedules, saved?.id, !!draft]);
  const state =
    draft ||
    (saved ? { ...saved.state, expected_version: saved.version } : null);
  const content = state?.content;
  const blocks = content?.blocks || [];
  const pending = tasks.filter((t) => t.status === "pending");
  const titles: Record<string, string> = {
    ...saved?.state.task_titles,
    ...Object.fromEntries(tasks.map((t) => [t.id, t.title])),
  };
  const dayCount = content?.days || days,
    startDate = content?.start_date || start;
  const dates = Array.from({ length: dayCount }, (_, i) =>
    addDays(startDate, i),
  );
  const visibleDay = dates.includes(activeDay) ? activeDay : dates[0];
  const shownEvents: TimeWindowTitle[] = [
    ...events,
    ...(content?.fixed_events || []),
  ].filter(
    (e, i, all) =>
      all.findIndex(
        (x) => x.start === e.start && x.end === e.end && x.title === e.title,
      ) === i,
  );
  const dayStart = midnight(visibleDay, zone),
    dayEnd = midnight(addDays(visibleDay, 1), zone);
  const rows = [
    ...blocks.map((b, i) => ({
      ...b,
      title: titles[b.task_id] || "کار",
      kind: "task" as const,
      index: i,
    })),
    ...shownEvents.map((e) => ({
      ...e,
      locked: true,
      title: e.title,
      kind: "event" as const,
      index: -1,
      task_id: "",
    })),
  ]
    .filter(
      (b) =>
        Date.parse(b.start) < Date.parse(dayEnd) &&
        Date.parse(b.end) > Date.parse(dayStart),
    )
    .sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
  const total = blocks.reduce((sum, b) => sum + duration(b.start, b.end), 0);
  function canDiscard() {
    return !draft || confirm("تغییرهای ذخیره‌نشده کنار گذاشته شوند؟");
  }
  function reset() {
    if (!canDiscard()) return;
    setSaved(null);
    setDraft(null);
    setSelected([]);
    setError("");
    setNotice("");
    setStart(today(zone));
    setActiveDay(today(zone));
  }
  async function run(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function open(id: string) {
    if (!id) return reset();
    if (!canDiscard()) return;
    await run(async () => {
      const s = await api<Saved>(`/schedules/${id}`);
      setSaved(s);
      setDraft(null);
      setActiveDay(s.state.content.start_date);
    });
  }
  function makeDraft(): Draft {
    if (draft) return structuredClone(draft);
    if (!saved) throw new Error("ابتدا برنامه بساز.");
    return {
      title: saved.state.title,
      content: structuredClone(saved.state.content),
      task_versions: saved.state.task_versions,
      preference_version: saved.state.preference_version,
      unscheduled: saved.state.unscheduled,
      warnings: saved.state.warnings,
      expected_version: saved.version,
    };
  }
  async function preview() {
    await run(async () => {
      if (!selected.length) throw new Error("حداقل یک کار را انتخاب کن.");
      if (to <= from) throw new Error("پایان وقت آزاد باید بعد از شروع باشد.");
      const input = {
        start_date: start,
        days,
        task_ids: selected,
        availability: Array.from({ length: days }, (_, i) => ({
          start: instant(addDays(start, i) + "T" + from, zone),
          end: instant(addDays(start, i) + "T" + to, zone),
        })),
        fixed_events: [],
      };
      const p = await api<Preview>("/schedules/preview", "POST", input);
      setDraft({
        title:
          days === 1
            ? "برنامهٔ " + dateLabel(start)
            : "هفتهٔ " + dateLabel(start),
        content: { ...input, blocks: p.blocks },
        task_versions: p.task_versions,
        preference_version: p.preference_version,
        unscheduled: p.unscheduled,
        warnings: p.warnings,
      });
      setActiveDay(start);
    });
  }
  async function replan() {
    if (!saved || !canDiscard()) return;
    await run(async () => {
      const result = await api<{ expected_version: number; preview: Preview }>(
        `/schedules/${saved.id}/replan`,
        "POST",
        {
          expected_version: saved.version,
          fixed_events: saved.state.content.fixed_events,
        },
      );
      const p = result.preview;
      setDraft({
        title: saved.state.title,
        content: { ...saved.state.content, blocks: p.blocks },
        task_versions: p.task_versions,
        preference_version: p.preference_version,
        unscheduled: p.unscheduled,
        warnings: p.warnings,
        expected_version: result.expected_version,
      });
      setNotice("پیشنهاد آماده است؛ زمان‌ها را بررسی و سپس ذخیره کن.");
    });
  }
  async function save() {
    if (!draft) return;
    await run(async () => {
      const { unscheduled: _, warnings: __, ...body } = draft;
      const result = await command<{ schedule: Saved }>(
        saved ? `/schedules/${saved.id}` : "/schedules",
        saved ? "PUT" : "POST",
        body,
      );
      setSaved(result.schedule);
      setDraft(null);
      setNotice("برنامه با موفقیت ذخیره شد.");
      await onChanged();
    });
  }
  async function travel(direction: "undo" | "redo") {
    if (!saved || draft) return;
    await run(async () => {
      const result = await command<{ schedule: Saved }>(
        `/schedules/${saved.id}/${direction}`,
        "POST",
        { expected_version: saved.version },
      );
      setSaved(result.schedule);
      setNotice(
        direction === "undo"
          ? "تغییر قبلی برگشت داده شد."
          : "تغییر دوباره اعمال شد.",
      );
      await onChanged();
    });
  }
  function changeBlock(index: number, change: Partial<Block>) {
    const d = makeDraft();
    d.content.blocks[index] = { ...d.content.blocks[index], ...change };
    setDraft(d);
  }
  function removeBlock(index: number) {
    const d = makeDraft();
    d.content.blocks.splice(index, 1);
    setDraft(d);
  }
  return (
    <>
      <div className="section-head">
        <div>
          <span className="eyebrow">برای چیزهای مهم، وقت بساز</span>
          <h1>
            برنامهٔ من<span className="heading-dot">.</span>
          </h1>
          <p className="muted">
            یک نگاه روشن به روزت؛ یک قدم نزدیک‌تر به کارهایت.
          </p>
        </div>
        <button className="primary" onClick={onTasks}>
          <Plus size={18} />
          افزودن کار
        </button>
      </div>
      <div className="stats">
        <div className="stat">
          <span className="stat-icon teal">
            <ListChecks size={21} />
          </span>
          <div>
            <small>کارهای در انتظار</small>
            <strong>
              {number(pending.length)} <em>کار</em>
            </strong>
          </div>
        </div>
        <div className="stat">
          <span className="stat-icon orange">
            <Clock3 size={21} />
          </span>
          <div>
            <small>زمان برنامه‌ریزی‌شده</small>
            <strong>
              {number(total)} <em>دقیقه</em>
            </strong>
          </div>
        </div>
        <div className="stat">
          <span className="stat-icon violet">
            <CheckCircle2 size={21} />
          </span>
          <div>
            <small>کارهای انجام‌شده</small>
            <strong>
              {number(tasks.filter((t) => t.status === "completed").length)}{" "}
              <em>کار</em>
            </strong>
          </div>
        </div>
      </div>
      <ErrorBox text={error} />
      {error && saved && (
        <button
          className="text-button"
          disabled={busy}
          onClick={() => open(saved.id)}
        >
          دریافت آخرین نسخهٔ برنامه
        </button>
      )}
      {notice && (
        <p className="notice success" role="status">
          {notice}
        </p>
      )}
      {saved?.sources.stale && (
        <div className="notice warning">
          <strong>این برنامه به بررسی نیاز دارد.</strong>
          <span>
            {saved.sources.fixed_event_conflicts.length
              ? `تداخل با ${saved.sources.fixed_event_conflicts.map((c) => c.title).join("، ")}؛ زمان کار را ویرایش کن یا بازچینی پیشنهادی را ببین.`
              : "کارها یا ترجیح‌ها از زمان ذخیره تغییر کرده‌اند. بازچینی پیشنهادی را بررسی کن."}
          </span>
        </div>
      )}
      <div className="planner-layout">
        <section className="panel calendar-panel">
          <div className="calendar-head">
            <div className="inline">
              <CalendarDays size={20} />
              <h2>{dateLabel(visibleDay)}</h2>
            </div>
            <span className={"badge " + (draft ? "amber" : "green")}>
              {draft
                ? "ذخیره‌نشده"
                : saved
                  ? "ذخیره‌شده"
                  : "آمادهٔ برنامه‌ریزی"}
            </span>
          </div>
          <div className="calendar-controls">
            <select
              aria-label="برنامهٔ ذخیره‌شده"
              value={saved?.id || ""}
              disabled={busy}
              onChange={(e) => open(e.target.value)}
            >
              <option value="">برنامهٔ جدید</option>
              {schedules.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.title}
                </option>
              ))}
            </select>
            <div className="inline">
              <button
                className="icon-button"
                aria-label="روز قبل"
                disabled={busy || dates.indexOf(visibleDay) === 0}
                onClick={() => setActiveDay(addDays(visibleDay, -1))}
              >
                <ChevronRight size={18} />
              </button>
              <button
                className="icon-button"
                aria-label="روز بعد"
                disabled={
                  busy || dates.indexOf(visibleDay) === dates.length - 1
                }
                onClick={() => setActiveDay(addDays(visibleDay, 1))}
              >
                <ChevronLeft size={18} />
              </button>
            </div>
          </div>
          {dayCount === 7 && (
            <div className="week-strip" role="group" aria-label="روزهای هفته">
              {dates.map((d) => (
                <button
                  key={d}
                  className={d === visibleDay ? "active" : ""}
                  onClick={() => setActiveDay(d)}
                >
                  {dateLabel(d)}
                </button>
              ))}
            </div>
          )}
          {!state ? (
            <Empty
              title="روزت هنوز یک صفحهٔ باز است"
              detail="کارها و زمان آزادت را انتخاب کن تا اولین برنامه‌ات را ببینی."
              action={
                <span className="empty-hint">
                  از بخش «ساخت برنامه» شروع کن <ArrowLeft size={16} />
                </span>
              }
            />
          ) : rows.length ? (
            <div className="timeline">
              {rows.map((b, i) => (
                <article
                  key={`${b.kind}-${b.index}-${i}`}
                  className={"timeline-row " + b.kind}
                >
                  <div className="timeline-time">
                    <strong>{clock(b.start, zone)}</strong>
                    <small>{clock(b.end, zone)}</small>
                  </div>
                  <span className="timeline-node" />
                  <div
                    className={
                      "schedule-block " + (b.kind === "event" ? "fixed" : "")
                    }
                  >
                    <div className="block-content">
                      <div>
                        <span className="block-type">
                          {b.kind === "event"
                            ? "تعهد ثابت"
                            : b.locked
                              ? "کار قفل‌شده"
                              : "زمان تمرکز"}
                        </span>
                        <h3>{b.title}</h3>
                        <small>
                          {number(duration(b.start, b.end))} دقیقه
                          {b.kind === "event" ? " · غیرقابل جابه‌جایی" : ""}
                        </small>
                      </div>
                      {b.kind === "event" ? (
                        <LockKeyhole size={18} />
                      ) : (
                        <div className="block-actions">
                          <button
                            className="icon-button"
                            disabled={busy || b.locked}
                            aria-label={`جابه‌جایی ${b.title}`}
                            onClick={() => setMoving(b.index)}
                          >
                            <Pencil size={17} />
                          </button>
                          <button
                            className="icon-button"
                            disabled={busy}
                            aria-label={`${b.locked ? "بازکردن قفل" : "قفل‌کردن"} ${b.title}`}
                            onClick={() =>
                              changeBlock(b.index, { locked: !b.locked })
                            }
                          >
                            {b.locked ? (
                              <LockKeyhole size={17} />
                            ) : (
                              <LockKeyholeOpen size={17} />
                            )}
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <Empty
              title="برای این روز کاری زمان‌بندی نشده"
              detail="اگر کاری جا نشده، مدت و مهلت یا زمان آزاد را بررسی کن."
            />
          )}
          <div className="calendar-footer">
            <span className="muted small">همهٔ زمان‌ها به وقت {zone}</span>
            {saved && (
              <span className="muted small">نسخهٔ {number(saved.version)}</span>
            )}
          </div>
        </section>
        <aside className="planner-aside">
          {!saved && !draft ? (
            <section className="panel setup">
              <h2>
                <Sparkles size={18} />
                ساخت برنامه
              </h2>
              <p className="muted small">
                کارها را انتخاب کن؛ ساعت‌ها را ما پیشنهاد می‌دهیم.
              </p>
              <fieldset disabled={busy}>
                <Field
                  label="تاریخ شروع"
                  hint="تاریخ ورودی میلادی است؛ نمایش تقویم شمسی است."
                >
                  <input
                    type="date"
                    value={start}
                    min={today(zone)}
                    onChange={(e) => e.target.value && setStart(e.target.value)}
                  />
                </Field>
                <div className="segmented">
                  <button
                    className={days === 1 ? "active" : ""}
                    onClick={() => setDays(1)}
                  >
                    یک روز
                  </button>
                  <button
                    className={days === 7 ? "active" : ""}
                    onClick={() => setDays(7)}
                  >
                    یک هفته
                  </button>
                </div>
                <div className="form-grid">
                  <Field label="آزاد از">
                    <input
                      type="time"
                      value={from}
                      onChange={(e) => setFrom(e.target.value)}
                    />
                  </Field>
                  <Field label="تا ساعت">
                    <input
                      type="time"
                      value={to}
                      onChange={(e) => setTo(e.target.value)}
                    />
                  </Field>
                </div>
                <div className="select-heading">
                  <strong>کارهای این برنامه</strong>
                  <button
                    className="text-button"
                    onClick={() =>
                      setSelected(
                        selected.length
                          ? []
                          : pending.slice(0, 100).map((t) => t.id),
                      )
                    }
                  >
                    {selected.length ? "برداشتن همه" : "انتخاب همه"}
                  </button>
                </div>
                <div className="task-picker">
                  {pending.length ? (
                    pending.map((t) => (
                      <label key={t.id} className="picker-row">
                        <input
                          type="checkbox"
                          checked={selected.includes(t.id)}
                          disabled={
                            !selected.includes(t.id) && selected.length >= 100
                          }
                          onChange={(e) =>
                            setSelected(
                              e.target.checked
                                ? [...selected, t.id]
                                : selected.filter((id) => id !== t.id),
                            )
                          }
                        />
                        <span>
                          {t.title}
                          <small>{number(t.duration_minutes)} دقیقه</small>
                        </span>
                      </label>
                    ))
                  ) : (
                    <p className="muted small">
                      ابتدا یک کار از بخش «کارهای من» اضافه کن.
                    </p>
                  )}
                </div>
                <button
                  className="primary wide"
                  disabled={!selected.length}
                  onClick={preview}
                >
                  <Sparkles size={17} />
                  {busy ? "در حال چیدن برنامه…" : "پیش‌نمایش برنامه"}
                </button>
              </fieldset>
            </section>
          ) : (
            <section className="panel setup">
              <h2>کنترل برنامه</h2>
              <p className="muted small">
                {draft
                  ? "پیشنهاد را مرور کن. تا ذخیره نکنی، برنامهٔ قبلی تغییر نمی‌کند."
                  : "می‌توانی زمان کارها را ویرایش یا بازچینی پیشنهادی را بررسی کنی."}
              </p>
              <fieldset disabled={busy}>
                <Field label="نام برنامه">
                  <input
                    value={state?.title || ""}
                    maxLength={200}
                    onChange={(e) => {
                      const d = makeDraft();
                      d.title = e.target.value;
                      setDraft(d);
                    }}
                  />
                </Field>
                {draft && (
                  <button
                    className="primary wide"
                    disabled={!draft.title.trim()}
                    onClick={save}
                  >
                    <Save size={17} />
                    {busy ? "در حال ذخیره…" : "ذخیرهٔ برنامه"}
                  </button>
                )}
                {saved && (
                  <>
                    <button className="secondary wide" onClick={replan}>
                      <Sparkles size={17} />
                      بازچینی پیشنهادی
                    </button>
                    <div className="form-grid history-buttons">
                      <button
                        className="secondary"
                        disabled={!!draft || !saved.can_undo}
                        onClick={() => travel("undo")}
                      >
                        <Undo2 size={17} />
                        برگشت
                      </button>
                      <button
                        className="secondary"
                        disabled={!!draft || !saved.can_redo}
                        onClick={() => travel("redo")}
                      >
                        <Redo2 size={17} />
                        اعمال مجدد
                      </button>
                    </div>
                  </>
                )}
                {draft && (
                  <button
                    className="text-button"
                    onClick={() => {
                      if (canDiscard()) setDraft(null);
                    }}
                  >
                    کنار گذاشتن تغییرها
                  </button>
                )}
                <button className="text-button" onClick={reset}>
                  <Plus size={16} />
                  ساخت برنامهٔ دیگر
                </button>
              </fieldset>
              {draft && (
                <small className="muted">
                  برای جابه‌جایی کار قفل‌شده، ابتدا بازکردن قفل را ذخیره کن.
                </small>
              )}
            </section>
          )}
          {state && state.unscheduled.length > 0 && (
            <section className="panel setup">
              <h2>کارهای جا نشده</h2>
              {state.unscheduled.map((t) => (
                <div className="unscheduled" key={t.task_id}>
                  <strong>{t.title}</strong>
                  <p>
                    {number(t.remaining_minutes)} دقیقه · {t.message}
                  </p>
                </div>
              ))}
            </section>
          )}
          {state && state.warnings.length > 0 && (
            <details className="panel setup">
              <summary>
                نکته‌های برنامه ({number(state.warnings.length)})
              </summary>
              {state.warnings.map((w, i) => (
                <p className="small muted" key={i}>
                  {w}
                </p>
              ))}
            </details>
          )}
          <div className="gentle-note">
            <span>یادت باشد</span>
            <p>
              لازم نیست همه‌چیز را امروز انجام بدهی.
              <br />
              برای استراحت هم جا بگذار.
            </p>
            <span className="leaf">✳</span>
          </div>
        </aside>
      </div>
      {moving !== null && (
        <MoveBlock
          block={blocks[moving]}
          title={titles[blocks[moving].task_id]}
          zone={zone}
          onClose={() => setMoving(null)}
          onApply={(b) => {
            changeBlock(moving, b);
            setMoving(null);
          }}
          onRemove={() => {
            removeBlock(moving);
            setMoving(null);
          }}
        />
      )}
    </>
  );
}
function MoveBlock({
  block,
  title,
  zone,
  onClose,
  onApply,
  onRemove,
}: {
  block: Block;
  title: string;
  zone: string;
  onClose: () => void;
  onApply: (b: Block) => void;
  onRemove: () => void;
}) {
  const [error, setError] = useState("");
  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    try {
      const f = new FormData(e.currentTarget);
      const start = instant(String(f.get("start")), zone);
      const end = new Date(
        Date.parse(start) + duration(block.start, block.end) * 60000,
      ).toISOString();
      onApply({ ...block, start, end });
    } catch (e) {
      setError(message(e));
    }
  }
  return (
    <Modal title={`جابه‌جایی ${title}`} onClose={onClose}>
      <form onSubmit={submit}>
        <Field
          label="شروع جدید"
          hint={`به وقت ${zone}؛ مدت کار ${number(duration(block.start, block.end))} دقیقه ثابت می‌ماند.`}
        >
          <input
            name="start"
            type="datetime-local"
            defaultValue={localInput(block.start, zone)}
            required
            autoFocus
          />
        </Field>
        <ErrorBox text={error} />
        <p className="muted small">
          این تغییر ابتدا در پیش‌نمایش اعمال می‌شود؛ برای بررسی تداخل و ثبت
          نهایی، برنامه را ذخیره کن.
        </p>
        <div className="modal-actions">
          <button className="primary">اعمال در پیش‌نمایش</button>
          <button
            className="text-button danger"
            type="button"
            onClick={onRemove}
          >
            برداشتن از برنامه
          </button>
        </div>
      </form>
    </Modal>
  );
}
