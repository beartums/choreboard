import { describe, expect, it } from "vitest";
import type { Rule } from "./api";
import { isRotationRule, overlaps, planRotation, rotationOf, rotations } from "./rotation";

const rule = (id: number, person_id: number, weekdays: Rule["weekdays"], extra: Partial<Rule> = {}): Rule => ({
  id, chore_id: 1, person_id, weekdays, every_days: null, anchor: null, on: null, available: null, due: "18:00", start: null, end: null, ...extra,
});
// The dog: Tucker Mon/Wed, Cass Tue/Sat, Mom Thu/Fri, Eric Sun.
const DOG = [rule(1, 2, [1, 3]), rule(2, 3, [2, 6]), rule(3, 4, [4, 5]), rule(4, 1, [7])];

describe("rotation", () => {
  it("reads the grid from weekday rules", () => {
    expect(rotationOf(DOG)).toEqual({ 1: 2, 3: 2, 2: 3, 6: 3, 4: 4, 5: 4, 7: 1 });
  });

  it("owns weekday rules, with or without dates", () => {
    expect(isRotationRule(rule(9, 1, [1], { end: "2026-12-01" }))).toBe(true);
    expect(isRotationRule(rule(9, 1, null, { every_days: 2, anchor: "2026-10-01" }))).toBe(false);
    expect(isRotationRule(rule(9, 1, null, { on: "2026-10-01" }))).toBe(false);
  });

  it("groups rotations by their date range, earliest first", () => {
    const summer = [rule(10, 2, [1, 2, 3, 4, 5], { start: "2027-06-15", end: "2027-08-31" })];
    const groups = rotations([...summer, ...DOG]);
    expect(groups.map((g) => [g.start, g.end, g.rules.length])).toEqual([[null, null, 4], ["2027-06-15", "2027-08-31", 1]]);
  });

  it("a start date on a rotation updates all its rules", () => {
    const plan = planRotation(1, DOG, rotationOf(DOG), "18:00", "2026-11-01", null);
    expect(plan.update).toHaveLength(4);
    expect(plan.update.every((u) => u.rule.start === "2026-11-01" && u.rule.end === null)).toBe(true);
  });

  it("a new rotation starting later is all creates", () => {
    const plan = planRotation(1, [], { 1: 3, 2: 3 }, null, "2026-12-01", null);
    expect(plan.create).toEqual([expect.objectContaining({ person_id: 3, weekdays: [1, 2], start: "2026-12-01" })]);
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

describe("overlaps", () => {
  it("treats missing dates as open-ended", () => {
    expect(overlaps({ start: null, end: null }, { start: "2027-06-15", end: "2027-08-31" })).toBe(true);
    expect(overlaps({ start: null, end: "2027-06-14" }, { start: "2027-06-15", end: null })).toBe(false);
    expect(overlaps({ start: "2027-06-15", end: "2027-06-15" }, { start: "2027-06-15", end: null })).toBe(true);
  });
});

import { timeWindow } from "./api";
describe("timeWindow", () => {
  it("reads naturally", () => {
    expect(timeWindow("16:00", "18:00")).toBe("4–6 PM");
    expect(timeWindow("09:30", "13:00")).toBe("9:30 AM–1 PM");
    expect(timeWindow("16:00", null)).toBe("after 4 PM");
    expect(timeWindow(null, "18:00")).toBe("by 6 PM");
    expect(timeWindow(null, null)).toBe("");
  });
  it("a window change updates the rotation's rules", () => {
    expect(planRotation(1, DOG, rotationOf(DOG), "18:00", null, null, "16:00").update).toHaveLength(4);
  });
});
