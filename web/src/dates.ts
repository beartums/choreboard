// Calendar dates as plain YYYY-MM-DD strings in the household's local time --
// the same days the API speaks. Never via toISOString(), which is UTC and
// shifts the day in the evening.

export function iso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function parse(s: string): Date {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function addDays(s: string, n: number): string {
  const d = parse(s);
  d.setDate(d.getDate() + n);
  return iso(d);
}

/** The Monday on or before a date. */
export function weekStart(s: string): string {
  const d = parse(s);
  return addDays(s, -((d.getDay() + 6) % 7));
}

export function today(): string {
  return iso(new Date());
}

export function dayLabel(s: string, now = today()): string {
  if (s === now) return "Today";
  if (s === addDays(now, 1)) return "Tomorrow";
  if (s === addDays(now, -1)) return "Yesterday";
  return parse(s).toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
}
