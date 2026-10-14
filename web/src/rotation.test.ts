import { describe, expect, it } from "vitest";
import type { Rule } from "./api";
import { isRotationRule, planRotation, rotationOf } from "./rotation";

const rule = (id: number, person_id: number, weekdays: Rule["weekdays"], extra: Partial<Rule> = {}): Rule => ({
  id, chore_id: 1, person_id, weekdays, every_days: null, anchor: null, on: null, due: "18:00", start: null, end: null, ...extra,
});
// The dog: Tucker Mon/Wed, Cass Tue/Sat, Mom Thu/Fri, Eric Sun.
const DOG = [rule(1, 2, [1, 3]), rule(2, 3, [2, 6]), rule(3, 4, [4, 5]), rule(4, 1, [7])];

describe("rotation", () => {
  it("reads the grid from weekday rules", () => {
    expect(rotationOf(DOG)).toEqual({ 1: 2, 3: 2, 2: 3, 6: 3, 4: 4, 5: 4, 7: 1 });
  });

  it("only owns plain weekday rules", () => {
    expect(isRotationRule(rule(9, 1, [1], { end: "2026-12-01" }))).toBe(false);
    expect(isRotationRule(rule(9, 1, null, { every_days: 2, anchor: "2026-10-01" }))).toBe(false);
  });

  it("does nothing when nothing changed", () => {
    expect(planRotation(1, DOG, rotationOf(DOG), "18:00")).toEqual({ create: [], update: [], remove: [] });
  });

  it("updates a person's existing rule in place, keeping its id", () => {
    // Tucker takes Thursday from Mom.
    const plan = planRotation(1, DOG, { ...rotationOf(DOG), 4: 2 }, "18:00");
    expect(plan.create).toEqual([]);
    expect(plan.remove).toEqual([]);
    expect(plan.update.map((u) => [u.id, u.rule.weekdays])).toEqual([[1, [1, 3, 4]], [3, [5]]]);
  });

  it("creates a rule for someone new and removes one for someone gone", () => {
    const { 7: _eric, ...noSunday } = rotationOf(DOG);
    const plan = planRotation(1, DOG, { ...noSunday, 7: 5 }, "18:00");
    expect(plan.create.map((r) => [r.person_id, r.weekdays])).toEqual([[5, [7]]]);
    expect(plan.remove).toEqual([4]);
  });

  it("a due-time change updates every rule", () => {
    expect(planRotation(1, DOG, rotationOf(DOG), "19:00").update).toHaveLength(4);
  });
});
