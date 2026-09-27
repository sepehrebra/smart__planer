import { useState, type FormEvent } from "react";
import { Plus, Pencil, Trash2, Check, Search, RotateCcw } from "lucide-react";
import { api, command, message } from "./api";
import { Field, Modal, ErrorBox, Empty } from "./ui";
import { instant, localInput, number } from "./time";
import type { Task } from "./types";
export function Tasks({
  tasks,
  zone,
  onChanged,
}: {
  tasks: Task[];
  zone: string;
  onChanged: () => Promise<void>;
}) {
  const [editing, setEditing] = useState<Task | "new" | null>(null),
    [search, setSearch] = useState(""),
    [filter, setFilter] = useState("pending"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const shown = tasks.filter(
    (t) =>
      (filter === "all" || t.status === filter) && t.title.includes(search),
  );
  async function change(task: Task, remove = false) {
    if (remove && !confirm(`«${task.title}» حذف شود؟`)) return;
    setBusy(true);
    setError("");
    try {
      if (remove)
        await api(
          `/tasks/${task.id}?expected_version=${task.version}`,
          "DELETE",
        );
      else
        await api(`/tasks/${task.id}`, "PATCH", {
          expected_version: task.version,
          status: task.status === "completed" ? "pending" : "completed",
        });
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
          <span className="eyebrow">هر کار، یک قدم رو به جلو</span>
          <h1>کارهای من</h1>
          <p className="muted">
            کارها را ثبت کن؛ بعد برایشان زمان پیدا می‌کنیم.
          </p>
        </div>
        <button className="primary" onClick={() => setEditing("new")}>
          <Plus size={18} />
          کار جدید
        </button>
      </div>
      <ErrorBox text={error} />
      <section className="panel">
        <div className="list-toolbar">
          <div className="search">
            <Search size={18} />
            <input
              aria-label="جست‌وجوی کارها"
              placeholder="جست‌وجوی کارها…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="segmented">
            {[
              ["pending", "در انتظار"],
              ["completed", "انجام‌شده"],
              ["all", "همه"],
            ].map(([k, v]) => (
              <button
                key={k}
                className={filter === k ? "active" : ""}
                onClick={() => setFilter(k)}
              >
                {v}
              </button>
            ))}
          </div>
        </div>
        {shown.length ? (
          <div className="task-list">
            {shown.map((t) => (
              <article className="task-row" key={t.id}>
                <button
                  disabled={busy}
                  className={
                    "check-button " +
                    (t.status === "completed" ? "checked" : "")
                  }
                  onClick={() => change(t)}
                  aria-label={
                    t.status === "completed"
                      ? `بازگرداندن ${t.title}`
                      : `انجام ${t.title}`
                  }
                >
                  {t.status === "completed" ? <Check size={16} /> : null}
                </button>
                <div className="grow">
                  <h3>{t.title}</h3>
                  <p>
                    {number(t.duration_minutes)} دقیقه{" "}
                    <span className="dot">·</span> اولویت {number(t.priority)}
                    {t.splittable ? " · قابل تقسیم" : ""}
                  </p>
                </div>
                {t.status === "completed" && (
                  <span className="badge green">انجام شد</span>
                )}
                <button
                  disabled={busy}
                  className="icon-button"
                  aria-label={`ویرایش ${t.title}`}
                  onClick={() => setEditing(t)}
                >
                  <Pencil size={17} />
                </button>
                <button
                  disabled={busy}
                  className="icon-button danger"
                  aria-label={`حذف ${t.title}`}
                  onClick={() => change(t, true)}
                >
                  <Trash2 size={17} />
                </button>
              </article>
            ))}
          </div>
        ) : (
          <Empty
            title={search ? "چیزی پیدا نشد" : "این فهرست هنوز خالی است"}
            detail="یک کار مشخص و کوچک، شروع خوبی برای امروز است."
            action={
              <button className="secondary" onClick={() => setEditing("new")}>
                <Plus size={16} />
                افزودن کار
              </button>
            }
          />
        )}
      </section>
      {editing && (
        <TaskEditor
          task={editing === "new" ? null : editing}
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
function TaskEditor({
  task,
  zone,
  onClose,
  onSaved,
}: {
  task: Task | null;
  zone: string;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError("");
    try {
      const body = {
        title: String(f.get("title")),
        description: String(f.get("description")),
        duration_minutes: Number(f.get("duration")),
        priority: Number(f.get("priority")),
        preferred_period: String(f.get("period")),
        splittable: f.get("splittable") === "on",
        earliest_start: f.get("earliest")
          ? instant(String(f.get("earliest")), zone)
          : null,
        deadline: f.get("deadline")
          ? instant(String(f.get("deadline")), zone)
          : null,
      };
      if (task)
        await api(`/tasks/${task.id}`, "PATCH", {
          ...body,
          expected_version: task.version,
        });
      else await command("/tasks", "POST", body);
      await onSaved();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={task ? "ویرایش کار" : "یک کار تازه"}
      onClose={() => !busy && onClose()}
    >
      <form onSubmit={submit}>
        <fieldset disabled={busy}>
          <Field label="عنوان کار">
            <input
              name="title"
              defaultValue={task?.title}
              placeholder="مثلاً مرور فصل جبر خطی"
              maxLength={200}
              required
              autoFocus
            />
          </Field>
          <div className="form-grid">
            <Field label="مدت (دقیقه)">
              <input
                type="number"
                name="duration"
                min={1}
                max={10080}
                defaultValue={task?.duration_minutes || 60}
                required
              />
            </Field>
            <Field label="اولویت (۱ تا ۱۰)">
              <input
                type="number"
                name="priority"
                min={1}
                max={10}
                defaultValue={task?.priority || 5}
                required
              />
            </Field>
          </div>
          <Field label="زمان ترجیحی">
            <select
              name="period"
              defaultValue={task?.preferred_period || "any"}
            >
              <option value="any">فرقی ندارد</option>
              <option value="morning">صبح</option>
              <option value="afternoon">بعدازظهر</option>
              <option value="evening">عصر و شب</option>
            </select>
          </Field>
          <details>
            <summary>مهلت و جزئیات بیشتر</summary>
            <Field
              label="زودترین شروع"
              hint={`زمان‌ها به وقت ${zone}؛ ورود تاریخ میلادی است.`}
            >
              <input
                type="datetime-local"
                name="earliest"
                defaultValue={
                  task?.earliest_start
                    ? localInput(task.earliest_start, zone)
                    : ""
                }
              />
            </Field>
            <Field label="مهلت پایان">
              <input
                type="datetime-local"
                name="deadline"
                defaultValue={
                  task?.deadline ? localInput(task.deadline, zone) : ""
                }
              />
            </Field>
            <Field label="یادداشت">
              <textarea
                name="description"
                maxLength={2000}
                defaultValue={task?.description}
              />
            </Field>
          </details>
          <label className="checkbox">
            <input
              type="checkbox"
              name="splittable"
              defaultChecked={task?.splittable}
            />
            می‌توان این کار را در چند بخش انجام داد
          </label>
          <ErrorBox text={error} />
          <div className="modal-actions">
            <button className="primary" type="submit">
              {busy ? "در حال ذخیره…" : "ذخیرهٔ کار"}
            </button>
            <button className="secondary" type="button" onClick={onClose}>
              انصراف
            </button>
          </div>
        </fieldset>
      </form>
    </Modal>
  );
}
