export interface User {
  id: string;
  email: string;
  timezone: string;
}
export interface Session {
  user: User;
  csrf_token: string;
}
export interface Task {
  id: string;
  title: string;
  description: string;
  duration_minutes: number;
  priority: number;
  status: "pending" | "completed" | "cancelled";
  version: number;
  preferred_period: string;
  splittable: boolean;
  earliest_start: string | null;
  deadline: string | null;
}
export interface TimeWindow {
  start: string;
  end: string;
}
export interface FixedEvent extends TimeWindow {
  title: string;
  id: string;
  version: number;
}
export interface Block extends TimeWindow {
  task_id: string;
  locked: boolean;
}
export interface PrefValues {
  chronotype: string;
  focus_block_minutes: number;
  break_minutes: number;
  flexibility: string;
  workload: string;
  deep_work_period: string;
}
export interface Prefs extends PrefValues {
  version: number;
}
export interface Content {
  start_date: string;
  days: number;
  task_ids: string[];
  availability: TimeWindow[];
  fixed_events: TimeWindowTitle[];
  blocks: Block[];
}
export interface TimeWindowTitle extends TimeWindow {
  title: string;
}
export interface Unscheduled {
  task_id: string;
  title: string;
  remaining_minutes: number;
  message: string;
}
export interface Preview {
  blocks: Block[];
  task_versions: Record<string, number>;
  preference_version: number;
  unscheduled: Unscheduled[];
  warnings: string[];
  fixed_events: TimeWindowTitle[];
}
export interface Saved {
  id: string;
  version: number;
  timezone: string;
  horizon: TimeWindow;
  can_undo: boolean;
  can_redo: boolean;
  state: Preview & {
    title: string;
    content: Content;
    task_titles: Record<string, string>;
  };
  sources: {
    stale: boolean;
    fixed_event_conflicts: {
      fixed_event_id: string;
      title: string;
      task_ids: string[];
    }[];
  };
}
export interface Summary {
  id: string;
  title: string;
  version: number;
  horizon: TimeWindow;
  timezone: string;
}
export interface Draft {
  title: string;
  content: Content;
  task_versions: Record<string, number>;
  preference_version: number;
  unscheduled: Unscheduled[];
  warnings: string[];
  expected_version?: number;
}
