import { useState, type FormEvent } from "react";
import { ArrowLeft, Check, CalendarDays, Sprout } from "lucide-react";
import { api, message, setCsrf } from "./api";
import { ErrorBox, Field } from "./ui";
import type { Session } from "./types";
export function Auth({ onLogin }: { onLogin: (session: Session) => void }) {
  const [register, setRegister] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [created, setCreated] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError("");
    try {
      const credentials = {
        email: String(f.get("email")),
        password: String(f.get("password")),
      };
      if (register) {
        await api("/auth/register", "POST", {
          ...credentials,
          timezone: String(f.get("timezone")),
        });
        setRegister(false);
        setCreated(true);
      }
      const session = await api<Session>("/auth/login", "POST", credentials);
      setCsrf(session.csrf_token);
      onLogin(session);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="auth">
      <section className="auth-story">
        <div className="brand light">
          <span className="brand-mark">
            <Sprout />
          </span>
          <div>
            اسمارت‌پلنر<small>SMARTPLANNER</small>
          </div>
        </div>
        <div>
          <span className="eyebrow">کمی نظم، کمی فضای نفس‌کشیدن</span>
          <h1>
            روزت را روشن‌تر
            <br />
            برنامه‌ریزی کن.
          </h1>
          <p>
            کارها، کلاس‌ها و زمان خودت را کنار هم ببین.
            <br />
            برنامه‌ای بساز که با زندگی تو هماهنگ باشد.
          </p>
          <div className="story-card">
            <CalendarDays />
            <div>
              <strong>همه‌چیز، در زمان خودش</strong>
              <span>برنامهٔ روز و هفته با حفظ تعهدهای ثابت</span>
            </div>
            <Check />
          </div>
        </div>
        <small>قدم‌های کوچک، روزهای بهتر.</small>
      </section>
      <section className="auth-form">
        <div className="auth-box">
          <span className="eyebrow">فضای شخصی برنامه‌ریزی تو</span>
          <h2>{register ? "از اینجا شروع کن" : "خوش برگشتی"}</h2>
          <p className="muted">
            {register
              ? "یک حساب بساز و برای روزت جا باز کن."
              : "وارد شو و ادامهٔ روزت را بساز."}
          </p>
          <div className="segmented">
            <button
              type="button"
              className={!register ? "active" : ""}
              onClick={() => {
                setRegister(false);
                setError("");
              }}
            >
              ورود
            </button>
            <button
              type="button"
              className={register ? "active" : ""}
              onClick={() => {
                setRegister(true);
                setError("");
              }}
            >
              ساخت حساب
            </button>
          </div>
          <ErrorBox text={error} />
          {created && (
            <p className="notice">حساب ساخته شد؛ با همان مشخصات وارد شوید.</p>
          )}
          <form onSubmit={submit}>
            <fieldset disabled={busy}>
              <Field label="ایمیل">
                <input
                  name="email"
                  type="email"
                  dir="ltr"
                  placeholder="you@example.com"
                  autoComplete="email"
                  required
                />
              </Field>
              <Field
                label="رمز عبور"
                hint="حداقل ۱۲ نویسه؛ فاصله‌ها هم بخشی از رمز هستند."
              >
                <input
                  name="password"
                  type="password"
                  dir="ltr"
                  minLength={12}
                  maxLength={128}
                  autoComplete={register ? "new-password" : "current-password"}
                  required
                />
              </Field>
              {register && (
                <Field label="منطقهٔ زمانی">
                  <select name="timezone" defaultValue="Asia/Tehran">
                    {[
                      ...new Set([
                        "Asia/Tehran",
                        Intl.DateTimeFormat().resolvedOptions().timeZone,
                        "UTC",
                        "Europe/London",
                        "America/New_York",
                      ]),
                    ].map((z) => (
                      <option key={z}>{z}</option>
                    ))}
                  </select>
                </Field>
              )}
              <button className="primary wide" type="submit">
                {busy
                  ? "در حال انجام…"
                  : register
                    ? "ساخت حساب و شروع"
                    : "ورود به برنامه"}
                <ArrowLeft size={18} />
              </button>
            </fieldset>
          </form>
          <p className="auth-foot">
            برنامه‌ریزی و ویرایش دستی به اتصال هوش مصنوعی نیاز ندارد.
          </p>
        </div>
      </section>
    </main>
  );
}
