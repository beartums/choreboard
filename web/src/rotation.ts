// Weekly rotations: "who does this chore on each weekday", saved as one
// weekday rule per person. A chore can have several rotations, each with its
// own start and end date (a school-year rotation and a summer one; a new
// rotation starting next month). Editing a rotation updates its rules IN
// PLACE rather than deleting and re-creating them, so their ids -- which
// recorded outcomes are keyed by -- survive.
import type { Rule, RuleInput, Weekday } from "./api";

/** day (ISO 1-7) -> person id, or absent for nobody. */
export type Rotation = Partial<Record<Weekday, number>>;

export interface RotationGroup {
  key: string;
  start: string | null;
  end: string | null;
  rules: Rule[];
}

/** A rule a rotation owns: a weekday rule. Every-N-days and one-date rules
 *  are "other schedules". */
export function isRotationRule(r: Rule): boolean {
  return !!r.weekdays?.length && !r.every_days && !r.on;
}

export const groupKey = (start: string | null, end: string | null) => `${start ?? ""}|${end ?? ""}`;

/** The chore's rotations, one per distinct (start, end), earliest first. */
export function rotations(rules: Rule[]): RotationGroup[] {
  const groups = new Map<string, RotationGroup>();
  for (const r of rules.filter(isRotationRule)) {
    const key = groupKey(r.start, r.end);
    if (!groups.has(key)) groups.set(key, { key, start: r.start, end: r.end, rules: [] });
    groups.get(key)!.rules.push(r);
  }
  return [...groups.values()].sort((a, b) => (a.start ?? "").localeCompare(b.start ?? ""));
}

export function rotationOf(rules: Rule[]): Rotation {
  const out: Rotation = {};
  for (const r of rules.filter(isRotationRule)) {
    for (const d of r.weekdays!) out[d] = r.person_id;
  }
  return out;
}

export interface RotationPlan {
  create: RuleInput[];
  update: { id: number; rule: RuleInput }[];
  remove: number[];
}

/** What to send to turn one rotation's rules into `want`, with its due time
 *  and date range. One rule per person: an existing rule for that person is
 *  updated (its first, if several); extra rules are removed; people new to
 *  the rotation get a rule. */
export function planRotation(choreId: number, rules: Rule[], want: Rotation, due: string | null,
                             start: string | null = null, end: string | null = null,
                             available: string | null = null): RotationPlan {
  const current = rules.filter(isRotationRule);
  const days = new Map<number, Weekday[]>();
  for (const [d, pid] of Object.entries(want)) {
    if (pid === undefined) continue;
    days.set(pid, [...(days.get(pid) ?? []), Number(d) as Weekday].sort());
  }
  const plan: RotationPlan = { create: [], update: [], remove: [] };
  const used = new Set<number>();
  for (const [pid, wd] of days) {
    const rule: RuleInput = { chore_id: choreId, person_id: pid, weekdays: wd, every_days: null, anchor: null,
                              on: null, available, due, start, end };
    const existing = current.find((r) => r.person_id === pid && !used.has(r.id));
    if (existing) {
      used.add(existing.id);
      const same = existing.due === due && (existing.available ?? null) === available
        && existing.start === start && existing.end === end
        && (existing.weekdays ?? []).join() === wd.join();
      if (!same) plan.update.push({ id: existing.id, rule });
    } else {
      plan.create.push(rule);
    }
  }
  plan.remove = current.filter((r) => !used.has(r.id)).map((r) => r.id);
  return plan;
}

/** Do two date ranges share a day? A missing start or end is open-ended. */
export function overlaps(a: { start: string | null; end: string | null }, b: { start: string | null; end: string | null }): boolean {
  const aStart = a.start ?? "0000-01-01", aEnd = a.end ?? "9999-12-31";
  const bStart = b.start ?? "0000-01-01", bEnd = b.end ?? "9999-12-31";
  return aStart <= bEnd && bStart <= aEnd;
}
