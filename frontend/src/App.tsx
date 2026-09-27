import { useCallback, useEffect, useRef, useState } from "react";
import {
  Sprout,
  LayoutDashboard,
  CheckSquare,
  CalendarDays,
  SlidersHorizontal,
  LogOut,
  RefreshCw,
  Menu,
  X,
} from "lucide-react";
import { api, allPages, ApiError, message, setCsrf } from "./api";
import { Auth } from "./Auth";
import { Planner } from "./Planner";
import { Tasks } from "./Tasks";
import { Events } from "./Events";
import { Preferences } from "./Preferences";
import { ErrorBox } from "./ui";
import { today, dateLabel } from "./time";
import type { Session, Task, FixedEvent, Prefs, Summary } from "./types";
type Tab = "planner" | "tasks" | "events" | "preferences";
const navigation = [
  { id: "planner" as const, label: "برنامهٔ من", icon: LayoutDashboard },
  { id: "tasks" as const, label: "کارهای من", icon: CheckSquare },
  { id: "events" as const, label: "کلاس‌ها و جلسه‌ها", icon: CalendarDays },
  { id: "preferences" as const, label: "ریتم شخصی", icon: SlidersHorizontal },
];
export function App() {
  const [session, setSession] = useState<Session | null>(null),
    [boot, setBoot] = useState(true),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [ready, setReady] = useState(false);
  const [tasks, setTasks] = useState<Task[]>([]),
    [events, setEvents] = useState<FixedEvent[]>([]),
    [prefs, setPrefs] = useState<Prefs | null>(null),
    [schedules, setSchedules] = useState<Summary[]>([]),
    [tab, setTab] = useState<Tab>("planner"),
    [dirty, setDirty] = useState(false),
    [menu, setMenu] = useState(false);
  const generation = useRef(0);
  const reset = useCallback(() => {
    generation.current++;
    setLoading(false);
    setSession(null);
    setCsrf("");
    setTasks([]);
    setEvents([]);
    setPrefs(null);
    setSchedules([]);
    setReady(false);
    setDirty(false);
    setTab("planner");
  }, []);
  async function bootstrap() {
    setBoot(true);
    setError("");
    try {
      const s = await api<Session>("/auth/session");
      setCsrf(s.csrf_token);
      setSession(s);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setError(message(e));
    } finally {
      setBoot(false);
    }
  }
  useEffect(() => {
    void bootstrap();
    window.addEventListener("session-expired", reset);
    return () => window.removeEventListener("session-expired", reset);
  }, [reset]);
  const refresh = useCallback(async () => {
    const current = ++generation.current;
    setLoading(true);
    setError("");
    try {
      const [t, e, p, s] = await Promise.all([
        allPages<Task>("/tasks"),
        allPages<FixedEvent>("/fixed-events"),
        api<Prefs>("/me/preferences"),
        allPages<Summary>("/schedules"),
      ]);
      if (current !== generation.current) return;
      setTasks(t);
      setEvents(e);
      setPrefs(p);
      setSchedules(s);
      setReady(true);
    } catch (e) {
      if (current === generation.current) setError(message(e));
      throw e;
    } finally {
      if (current === generation.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    if (session) void refresh().catch(() => {});
  }, [session, refresh]);
  function navigate(next: Tab) {
    if (
      next !== tab &&
      dirty &&
      !confirm("پیش‌نمایش ذخیره نشده است. تغییرها کنار گذاشته شوند؟")
    )
      return;
    setTab(next);
    setMenu(false);
  }
  async function logout() {
    if (dirty && !confirm("تغییرهای ذخیره‌نشده کنار گذاشته شوند و خارج شوی؟"))
      return;
    setLoading(true);
    try {
      await api("/auth/logout", "POST");
      reset();
    } catch (e) {
      setError(message(e));
    } finally {
      setLoading(false);
    }
  }
  if (boot)
    return (
      <div className="loading-screen">
        <Sprout size={36} />
        <p>در حال آماده‌کردن فضای تو…</p>
      </div>
    );
  if (!session)
    return (
      <>
        {error && (
          <div className="boot-error">
            <ErrorBox text={error} />
            <button className="secondary" onClick={bootstrap}>
              تلاش دوباره
            </button>
          </div>
        )}
        <Auth onLogin={setSession} />
      </>
    );
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        رفتن به محتوای اصلی
      </a>
      {menu && (
        <button
          className="nav-scrim"
          aria-label="بستن فهرست"
          onClick={() => setMenu(false)}
        />
      )}
      <aside className={"sidebar " + (menu ? "open" : "")}>
        <div className="brand">
          <span className="brand-mark">
            <Sprout size={25} />
          </span>
          <div>
            اسمارت‌پلنر<small>SMARTPLANNER</small>
          </div>
          <button
            className="mobile-only icon-button"
            aria-label="بستن فهرست"
            onClick={() => setMenu(false)}
          >
            <X size={20} />
          </button>
        </div>
        <p className="nav-caption">فضای برنامه‌ریزی</p>
        <nav aria-label="فهرست اصلی">
          {navigation.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              className={tab === id ? "active" : ""}
              aria-current={tab === id ? "page" : undefined}
              onClick={() => navigate(id)}
            >
              <Icon size={20} />
              {label}
              {id === "tasks" &&
                tasks.filter((t) => t.status === "pending").length > 0 && (
                  <span className="nav-count">
                    {new Intl.NumberFormat("fa-IR").format(
                      tasks.filter((t) => t.status === "pending").length,
                    )}
                  </span>
                )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-note">
            <Sprout size={22} />
            <strong>یک قدم در هر بار</strong>
            <p>
              برنامهٔ خوب برای زندگی تو
              <br />
              جا باز می‌کند.
            </p>
          </div>
          <div className="account">
            <span className="avatar">
              {session.user.email[0].toUpperCase()}
            </span>
            <div>
              <strong>حساب شخصی</strong>
              <span dir="ltr">{session.user.email}</span>
            </div>
            <button
              className="icon-button"
              aria-label="خروج از حساب"
              disabled={loading}
              onClick={logout}
            >
              <LogOut size={18} />
            </button>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="inline">
            <button
              className="mobile-only icon-button"
              aria-label="بازکردن فهرست"
              aria-expanded={menu}
              onClick={() => setMenu(true)}
            >
              <Menu />
            </button>
            <span>
              فضای من <span className="breadcrumb">/</span>{" "}
              {navigation.find((n) => n.id === tab)?.label}
            </span>
          </div>
          <div className="inline">
            <span className="top-date">
              {dateLabel(today(session.user.timezone))}
            </span>
            <button
              className={"icon-button " + (loading ? "spin" : "")}
              aria-label="تازه‌سازی اطلاعات"
              disabled={loading || dirty}
              onClick={() => refresh().catch(() => {})}
            >
              <RefreshCw size={17} />
            </button>
          </div>
        </header>
        <main id="main" className="main-content">
          <ErrorBox text={error} />
          {!ready ? (
            <section className="panel empty">
              <p>
                {loading
                  ? "در حال دریافت اطلاعات…"
                  : "دریافت اطلاعات کامل نشد."}
              </p>
              {!loading && (
                <button
                  className="secondary"
                  onClick={() => refresh().catch(() => {})}
                >
                  تلاش دوباره
                </button>
              )}
            </section>
          ) : (
            <>
              {tab === "planner" && (
                <Planner
                  tasks={tasks}
                  events={events}
                  schedules={schedules}
                  zone={session.user.timezone}
                  onChanged={refresh}
                  onDirty={setDirty}
                  onTasks={() => navigate("tasks")}
                />
              )}{" "}
              {tab === "tasks" && (
                <Tasks
                  tasks={tasks}
                  zone={session.user.timezone}
                  onChanged={refresh}
                />
              )}{" "}
              {tab === "events" && (
                <Events
                  events={events}
                  zone={session.user.timezone}
                  onChanged={refresh}
                />
              )}{" "}
              {tab === "preferences" && prefs && (
                <Preferences prefs={prefs} onChanged={refresh} />
              )}
            </>
          )}
        </main>
        <footer className="app-footer">
          اسمارت‌پلنر <span>·</span> برای روزهایی با تمرکز بیشتر و شلوغی کمتر
        </footer>
      </div>
    </div>
  );
}
