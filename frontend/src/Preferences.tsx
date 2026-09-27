import { useState, type FormEvent } from "react";
import { SlidersHorizontal, Save } from "lucide-react";
import { api, message } from "./api";
import { ErrorBox, Field } from "./ui";
import type { Prefs, PrefValues } from "./types";
export function Preferences({
  prefs,
  onChanged,
}: {
  prefs: Prefs;
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [success, setSuccess] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setSuccess(false);
    const f = new FormData(e.currentTarget);
    try {
      const values: PrefValues = {
        chronotype: String(f.get("chronotype")),
        focus_block_minutes: Number(f.get("focus")),
        break_minutes: Number(f.get("break")),
        flexibility: String(f.get("flexibility")),
        workload: String(f.get("workload")),
        deep_work_period: String(f.get("deep")),
      };
      await api("/me/preferences", "PUT", {
        expected_version: prefs.version,
        values,
      });
      await onChanged();
      setSuccess(true);
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
          <span className="eyebrow">برنامه‌ای شبیه خودت</span>
          <h1>ریتم شخصی</h1>
          <p className="muted">
            شش ترجیح ساده برای برنامه‌ریزی هماهنگ‌تر با روز تو.
          </p>
        </div>
        <SlidersHorizontal className="muted" />
      </div>
      <form className="panel prefs-panel" onSubmit={submit} key={prefs.version}>
        <fieldset disabled={busy}>
          <div className="form-grid">
            <Field label="چه موقع پرانرژی‌تری؟">
              <select name="chronotype" defaultValue={prefs.chronotype}>
                <option value="morning">صبح‌ها</option>
                <option value="neutral">تفاوتی ندارد</option>
                <option value="evening">عصر و شب</option>
              </select>
            </Field>
            <Field label="طول هر بازهٔ تمرکز">
              <select name="focus" defaultValue={prefs.focus_block_minutes}>
                {[25, 45, 60, 90].map((n) => (
                  <option key={n} value={n}>
                    {n} دقیقه
                  </option>
                ))}
              </select>
            </Field>
            <Field label="استراحت بین کارها (دقیقه)">
              <input
                name="break"
                type="number"
                min={0}
                max={60}
                defaultValue={prefs.break_minutes}
                required
              />
            </Field>
            <Field label="انعطاف برنامه">
              <select name="flexibility" defaultValue={prefs.flexibility}>
                <option value="low">کم؛ ثبات بیشتر</option>
                <option value="medium">متوسط</option>
                <option value="high">زیاد</option>
              </select>
            </Field>
            <Field label="حجم کار روزانه">
              <select name="workload" defaultValue={prefs.workload}>
                <option value="light">سبک</option>
                <option value="medium">متعادل</option>
                <option value="intense">پرفشار</option>
              </select>
            </Field>
            <Field label="زمان مطلوب کار عمیق">
              <select name="deep" defaultValue={prefs.deep_work_period}>
                <option value="morning">صبح</option>
                <option value="afternoon">بعدازظهر</option>
                <option value="evening">عصر و شب</option>
              </select>
            </Field>
          </div>
          <p className="notice">
            این ترجیح‌ها در برنامه‌ریزی بعدی استفاده می‌شوند. برای اعمال روی
            برنامهٔ ذخیره‌شده، «بازچینی پیشنهادی» را بزن.
          </p>
          <ErrorBox text={error} />
          {success && (
            <p role="status" className="notice success">
              ترجیح‌های برنامه‌ریزی ذخیره شد.
            </p>
          )}
          <button className="primary">
            <Save size={17} />
            {busy ? "در حال ذخیره…" : "ذخیرهٔ ترجیح‌ها"}
          </button>
        </fieldset>
      </form>
    </>
  );
}
