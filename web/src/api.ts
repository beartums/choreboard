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
  available: string | null;        // daily window start, "HH:MM"
  due: string | null;              // daily window end
  start: string | null;            // first day, if set
  end: string | null;              // last day
  /** When it actually begins: `start`, or the day it was set up. Read-only. */
  first_day?: string | null;
}

export type RuleInput = Omit<Rule, "id" | "first_day">;

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
  available: string | null;
  due: string | null;
  source: "rule" | "override";
  rule_id: number | null;
  scheduled_person_id: number | null;
  override_id: number | null;
  skipped: boolean;
  reason: string;
  group_id: string | null;
  /** Recorded, but no longer on the schedule (its rule changed or now begins later). */
  unscheduled?: boolean;
  outcome: Outcome | null;
  /** What the person asking may do here (the server enforces the same rules). */
  can?: string[];
  /** Why not, for the common actions: {"done": "opens at 4 PM"}. */
  cannot?: Record<string, string>;
  /** A tentative swap this is part of, awaiting the other side. */
  pending_swap?: { id: number; from_person: number; to_person: number; with: string; reason: string;
                   side: "from" | "to"; kind?: "swap" | "undo" } | null;
}

export interface LedgerEntry {
  id: number;
  person_id: number;
  delta: number;
  kind: "earned" | "deduction" | "adjustment" | "reversal";
  note: string;
  created_at: string;
}

export type Channel = "app" | "email" | "discord";
/** Channels with an address to set; "app" (the in-app inbox) needs none. */
export type SendChannel = Exclude<Channel, "app">;

export interface Catalog {
  discord_invite?: string | null;
  events: { kind: string; label: string; scope: "mine" | "household" | "admins" }[];
  channels: { channel: Channel; configured: boolean }[];
}

export interface Subscription {
  id: number;
  event: string;
  channel: Channel;
  chore_id: number | null;
}

export interface ChannelSetting {
  address: string | null;
  enabled: boolean;
  default: boolean;
}

export interface InboxItem {
  id: number;
  read_at: string | null;
  created_at: string;
  kind: string;
  title: string;
  body: string;
  chore_id: number | null;
  date: string | null;
}

export interface Delivery {
  id: number;
  channel: SendChannel;
  address: string;
  status: "queued" | "sending" | "sent" | "failed";
  attempts: number;
  last_error: string | null;
  sent_at: string | null;
  kind: string;
  title: string;
  body: string;
  created_at: string;
}

export interface SettingField {
  key: string;
  label: string;
  secret: boolean;
  help: string;
  source: "env" | "app" | "default";
  env: string;
  value?: string;          // non-secrets only
  default?: string;
  set?: boolean;           // secrets only: never the value
  problem?: string;
}

export interface ServerSettings {
  can_store_secrets: boolean;
  key_env: string;
  fields: SettingField[];
  configured: string[];
}

export type Scope = "read" | "write" | "act_as";

export interface ApiToken {
  id: number;
  name: string;
  prefix: string;
  person_id: number;
  person_name: string;
  scopes: Scope[];
  created_at: string;
  last_used: string | null;
  revoked_at: string | null;
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
  proposeSwap: (a: string, b: string, reason: string) => call<{ id: number }>("POST", "/api/swap-requests", { a, b, reason }),
  answerSwap: (id: number, approve: boolean, reason = "") =>
    call<{ status: string }>("POST", `/api/swap-requests/${id}/${approve ? "approve" : "decline"}`, { reason }),
  requestUndo: (key: string, reason: string) => call<{ id: number }>("POST", "/api/swap-requests/undo", { key, reason }),
  cancelSwap: (id: number) => call<{ status: string }>("POST", `/api/swap-requests/${id}/cancel`),
  removeOverride: (groupOrId: string | number) =>
    call<{ removed: number }>("DELETE", `/api/overrides/${groupOrId}`),
  record: (key: string, b: { status: Status; note?: string; done_by?: number; deduct?: number }) =>
    call<Outcome>("POST", `/api/occurrences/${key}/outcome`, b),
  undo: (outcomeId: number, note: string) => call<Outcome>("POST", `/api/outcomes/${outcomeId}/undo`, { note }),
  points: (personId: number) =>
    call<{ person_id: number; balance: number; entries: LedgerEntry[] }>("GET", `/api/people/${personId}/points?limit=20`),
  adjust: (person_id: number, delta: number, note: string) =>
    call<LedgerEntry>("POST", "/api/ledger", { person_id, delta, note }),

  catalog: () => call<Catalog>("GET", "/api/notifications/catalog"),
  myNotifications: () =>
    call<{ channels: Record<SendChannel, ChannelSetting>; subscriptions: Subscription[] }>("GET", "/api/me/notifications"),
  setChannel: (channel: SendChannel, address: string | null) =>
    call<Record<SendChannel, ChannelSetting>>("PUT", `/api/me/channels/${channel}`, { address, enabled: true }),
  inbox: (unread = false) => call<{ unread: number; items: InboxItem[] }>("GET", `/api/me/inbox${unread ? "?unread=true" : ""}`),
  markRead: (ids?: number[], read = true) =>
    call<{ changed: number; unread: number }>("POST", "/api/me/inbox/read", { ids: ids ?? null, read }),
  testChannel: (channel: SendChannel) => call<{ status: string }>("POST", `/api/me/channels/${channel}/test`),
  subscribe: (event: string, channel: Channel, chore_id: number | null = null) =>
    call<Subscription>("POST", "/api/me/subscriptions", { event, channel, chore_id }),
  unsubscribe: (id: number) => call<void>("DELETE", `/api/me/subscriptions/${id}`),
  deliveries: () => call<Delivery[]>("GET", "/api/me/deliveries?limit=30"),
  requestDiscord: () => call<{ asked: number; invite: string | null }>("POST", "/api/me/discord/request"),

  settings: () => call<ServerSettings>("GET", "/api/settings"),
  saveSettings: (values: Record<string, string | null>) => call<ServerSettings>("PUT", "/api/settings", { values }),

  tokens: () => call<ApiToken[]>("GET", "/api/tokens"),
  createToken: (name: string, scopes: Scope[], person_id?: number) =>
    call<ApiToken & { token: string }>("POST", "/api/tokens", { name, scopes, person_id }),
  revokeToken: (id: number) => call<ApiToken>("DELETE", `/api/tokens/${id}`),

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
export function describeRule(r: Pick<Rule, "weekdays" | "every_days" | "anchor" | "on" | "available" | "due" | "start" | "end">): string {
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
  const w = timeWindow(r.available, r.due);
  if (w) bits.push(w);
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

/** "4–6 PM", "4 PM–9 AM"... for a daily window; "after 4 PM" or "by 6 PM" for half of one. */
export function timeWindow(available: string | null, due: string | null): string {
  if (available && due) {
    const a = clock(available), d = clock(due);
    const [as, ds] = [a.split(" ")[1], d.split(" ")[1]];
    return as === ds ? `${a.split(" ")[0]}–${d}` : `${a}–${d}`;
  }
  if (available) return `after ${clock(available)}`;
  if (due) return `by ${clock(due)}`;
  return "";
}
