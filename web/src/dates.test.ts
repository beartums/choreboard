import { describe, expect, it } from "vitest";
import { addDays, addMonths, dayLabel, monthGrid, weekStart } from "./dates";

describe("dates", () => {
  it("weeks start on Monday", () => {
    expect(weekStart("2026-10-14")).toBe("2026-10-12");   // Wed -> Mon
    expect(weekStart("2026-10-12")).toBe("2026-10-12");   // Mon -> itself
    expect(weekStart("2026-10-18")).toBe("2026-10-12");   // Sun -> previous Mon
  });
  it("adds days across months and DST", () => {
    expect(addDays("2026-10-31", 1)).toBe("2026-11-01");
    expect(addDays("2026-11-01", 1)).toBe("2026-11-02"); // US DST ends Nov 1
    expect(addDays("2026-03-08", -1)).toBe("2026-03-07");
  });
  it("names nearby days", () => {
    expect(dayLabel("2026-10-14", "2026-10-14")).toBe("Today");
    expect(dayLabel("2026-10-15", "2026-10-14")).toBe("Tomorrow");
    expect(dayLabel("2026-10-13", "2026-10-14")).toBe("Yesterday");
  });
});

describe("months", () => {
  it("covers the month in whole Monday-Sunday weeks", () => {
    expect(monthGrid("2026-10-14")).toEqual(["2026-09-28", "2026-11-01"]);   // Oct 1 is a Thursday, Oct 31 a Saturday
    expect(monthGrid("2026-02-10")).toEqual(["2026-01-26", "2026-03-01"]);
  });
  it("steps by month from any day", () => {
    expect(addMonths("2026-01-31", 1)).toBe("2026-02-01");
    expect(addMonths("2026-01-15", -1)).toBe("2025-12-01");
  });
});
