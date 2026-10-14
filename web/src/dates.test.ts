import { describe, expect, it } from "vitest";
import { addDays, dayLabel, weekStart } from "./dates";

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
