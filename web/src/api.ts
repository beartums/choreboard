// A typed client for choreboard's HTTP API (choreboard/api.py). Every call
// goes to the same origin; Pomerium signs the browser in, so there is no
// token handling here.

export interface Person {
  id: number;
  name: string;
  color: string;
  email: string | null;
  role: "admin" | "member";
  active: number;
}

export interface Chore {
  id: number;
  title: string;
  description: string;
  icon: string;
  points: number;
  active: number;
}

/** ISO weekdays: 1 = Monday ... 7 = Sunday. */
export type Weekday = 1 | 2 | 3 | 4 | 5 | 6 | 7;

export interface Rule {
  id: number;
  chore_id: number;
  person_id: number;
  weekdays: Weekday[] | null;
  every_days: number | null;
  anchor: string | null;
  on: string | null;
  due: string | null;
  start: string | null;
  end: string | null;
}

export type RuleInput = Omit<Rule, "id">;

export type Status = "done" | "skipped" | "failed" | "covered" | "failed_covered";

export interface Outcome {
  id: number;
  status: Status;
  assignee_id: number;
  done_by_id: number | null;
  note: string;
  recorded_by: number | null;
  recorded_at: string;
}

/** One occurrence on the calendar, overrides applied (choreboard/service.py entries_json). */
export interface Entry {
  id: string;                      // occurrence id: chore:YYYY-MM-DD:rule|add:n
  chore_id: number;
  date: string;
  person_id: number;
  due: string | null;
  source: "rule" | "override";
  rule_id: number | null;
  scheduled_person_id: number | null;
  override_id: number | null;
  skipped: boolean;
  reason: string;
  group_id: string | null;
  outcome: Outcome | null;
}

export interface LedgerEntry {
  id: number;
  person_id: number;
  delta: number;
  kind: "earned" | "deduction" | "adjustment" | "reversal";
  note: string;
  created_at: string;
}

export interface Me {
  email: string;
  person: Person | null;
  via: string;
  scopes: string[];
  needs_setup: boolean;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    // Service errors are {error}; FastAPI validation errors are {detail: [...]}.
    const detail = Array.isArray(data.detail)
      ? data.detail.map((d: { loc?: unknown[]; msg: string }) => `${(d.loc ?? []).slice(-1)[0]}: ${d.msg}`).join("; ")
      : data.detail;
    throw new ApiError(res.status, data.error ?? detail ?? `HTTP ${res.status}`);
  }
  return data as T;
}

export const api = {
  me: () => call<Me>("GET", "/api/me"),
  setup: (name: string) => call<Person>("POST", "/api/setup", { name }),

  people: () => call<Person[]>("GET", "/api/people"),
  createPerson: (p: { name: string; email?: string; color?: string; role?: Person["role"] }) =>
    call<Person>("POST", "/api/people", p),
  updatePerson: (id: number, p: Partial<Omit<Person, "id" | "active">> & { active?: boolean }) =>
    call<Person>("PATCH", `/api/people/${id}`, p),

  chores: () => call<Chore[]>("GET", "/api/chores"),
  createChore: (c: { title: string; points?: number; description?: string; icon?: string }) =>
    call<Chore>("POST", "/api/chores", c),
  updateChore: (id: number, c: Partial<Omit<Chore, "id" | "active">> & { active?: boolean }) =>
    call<Chore>("PATCH", `/api/chores/${id}`, c),

  calendar: (from: string, to: string, personId?: number) =>
    call<Entry[]>("GET", `/api/calendar?from=${from}&to=${to}${personId ? `&person_id=${personId}` : ""}`),
  reassign: (key: string, person_id: number, reason: string) =>
    call<Entry>("POST", `/api/occurrences/${key}/reassign`, { person_id, reason }),
  skip: (key: string, reason: string) => call<Entry>("POST", `/api/occurrences/${key}/skip`, { reason }),
  addOccurrence: (b: { chore_id: number; date: string; person_id: number; reason: string; due?: string | null }) =>
    call<Entry>("POST", "/api/occurrences", b),
  swap: (a: string, b: string, reason: string) =>
    call<{ group_id: string; a: Entry; b: Entry }>("POST", "/api/swaps", { a, b, reason }),
  removeOverride: (groupOrId: string | number) =>
    call<{ removed: number }>("DELETE", `/api/overrides/${groupOrId}`),
  record: (key: string, b: { status: Status; note?: string; done_by?: number; deduct?: number }) =>
    call<Outcome>("POST", `/api/occurrences/${key}/outcome`, b),
  undo: (outcomeId: number, note: string) => call<Outcome>("POST", `/api/outcomes/${outcomeId}/undo`, { note }),
  points: (personId: number) =>
    call<{ person_id: number; balance: number; entries: LedgerEntry[] }>("GET", `/api/people/${personId}/points?limit=20`),
  adjust: (person_id: number, delta: number, note: string) =>
    call<LedgerEntry>("POST", "/api/ledger", { person_id, delta, note }),

  rules: (choreId?: number) => call<Rule[]>("GET", choreId ? `/api/rules?chore_id=${choreId}` : "/api/rules"),
  createRule: (r: RuleInput) => call<Rule>("POST", "/api/rules", r),
  updateRule: (id: number, r: RuleInput) => call<Rule>("PUT", `/api/rules/${id}`, r),
  deleteRule: (id: number) => call<void>("DELETE", `/api/rules/${id}`),
};

export const WEEKDAYS: { iso: Weekday; short: string }[] = [
  { iso: 1, short: "Mon" }, { iso: 2, short: "Tue" }, { iso: 3, short: "Wed" }, { iso: 4, short: "Thu" },
  { iso: 5, short: "Fri" }, { iso: 6, short: "Sat" }, { iso: 7, short: "Sun" },
];

/** "Mon, Wed · 6 PM", "Every 3 days from Oct 20", "Oct 22 only" -- how a rule reads. */
export function describeRule(r: Pick<Rule, "weekdays" | "every_days" | "anchor" | "on" | "due" | "start" | "end">): string {
  let when: string;
  if (r.weekdays?.length) {
    const days = [...r.weekdays].sort();
    when = days.length === 7 ? "Every day"
      : days.join() === "1,2,3,4,5" ? "Weekdays"
      : days.join() === "6,7" ? "Weekends"
      : days.map((d) => WEEKDAYS[d - 1].short).join(", ");
  } else if (r.every_days) {
    when = r.every_days === 1 ? `Every day from ${shortDate(r.anchor)}` : `Every ${r.every_days} days from ${shortDate(r.anchor)}`;
  } else {
    when = `${shortDate(r.on)} only`;
  }
  const bits = [when];
  if (r.due) bits.push(`by ${clock(r.due)}`);
  if (r.start && r.end) bits.push(`${shortDate(r.start)}–${shortDate(r.end)}`);
  else if (r.start) bits.push(`from ${shortDate(r.start)}`);
  else if (r.end) bits.push(`until ${shortDate(r.end)}`);
  return bits.join(" · ");
}

export function shortDate(iso: string | null): string {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function clock(hhmm: string): string {
  const [h, m] = hhmm.split(":").map(Number);
  const suffix = h < 12 ? "AM" : "PM";
  const h12 = h % 12 || 12;
  return m ? `${h12}:${String(m).padStart(2, "0")} ${suffix}` : `${h12} ${suffix}`;
}
