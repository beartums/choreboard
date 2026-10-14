// The weekly rotation: "who does this chore on each weekday", saved as one
// weekday rule per person. Editing the grid updates those rules IN PLACE
// rather than deleting and re-creating them, so their ids -- which recorded
// outcomes are keyed by -- survive.
import type { Rule, RuleInput, Weekday } from "./api";

/** day (ISO 1-7) -> person id, or absent for nobody. */
export type Rotation = Partial<Record<Weekday, number>>;

/** A rule the grid owns: weekdays only -- no interval, single date or date
 *  range. Anything else is shown and edited as an individual rule. */
export function isRotationRule(r: Rule): boolean {
  return !!r.weekdays?.length && !r.every_days && !r.on && !r.start && !r.end;
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

/** What to send to turn the current rotation rules into `want`. One rule per
 *  person: an existing rule for that person is updated (its first, if several);
 *  extra rules are removed; people new to the rotation get a rule. */
export function planRotation(choreId: number, rules: Rule[], want: Rotation, due: string | null): RotationPlan {
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
                              on: null, due, start: null, end: null };
    const existing = current.find((r) => r.person_id === pid && !used.has(r.id));
    if (existing) {
      used.add(existing.id);
      const same = existing.due === due && (existing.weekdays ?? []).join() === wd.join();
      if (!same) plan.update.push({ id: existing.id, rule });
    } else {
      plan.create.push(rule);
    }
  }
  plan.remove = current.filter((r) => !used.has(r.id)).map((r) => r.id);
  return plan;
}
