import { useState } from "react";
import { addDays, addMonths, dayLabel, monthGrid, monthLabel, monthStart, parse, today, weekStart } from "../dates";

/** One chore someone else could swap with. */
export interface Candidate { key: string; date: string; person: string; chore: string; window?: string }

type By = "chore" | "person";

/** Pick the other side of a swap in three small steps, the same in the app
 *  and the nav widget: by chore or by person; which one; then the other; then
 *  the date. Each list shows only what still has dates behind it. */
export function SwapPicker({ candidates, value, onChange, own }: {
  candidates: Candidate[]; value: string; onChange: (key: string) => void;
  /** The day being swapped away, marked on the calendar. */
  own?: string;
}) {
  const [by, setBy] = useState<By>("person");
  const [first, setFirst] = useState<string | null>(null);
  const [second, setSecond] = useState<string | null>(null);
  const firstOf = (c: Candidate) => (by === "person" ? c.person : c.chore);
  const secondOf = (c: Candidate) => (by === "person" ? c.chore : c.person);
  const uniq = (xs: string[]) => [...new Set(xs)].sort((a, b) => a.localeCompare(b));
  const firsts = uniq(candidates.map(firstOf));
  const afterFirst = candidates.filter((c) => firstOf(c) === first);
  const seconds = uniq(afterFirst.map(secondOf));
  const dates = afterFirst.filter((c) => secondOf(c) === second).sort((a, b) => a.date.localeCompare(b.date));

  const chips = (label: string, items: string[], picked: string | null, pick: (x: string) => void) => (
    <div className="pick-step" role="group" aria-label={label}>
      <span className="muted">{label}</span>
      {items.map((x) => (
        <button type="button" key={x} className={`chip${picked === x ? " on" : ""}`} aria-pressed={picked === x}
          onClick={() => pick(x)}>{x}</button>
      ))}
    </div>
  );

  if (!candidates.length) return <p className="muted">Nothing of anyone else's to swap with in this range.</p>;
  return (
    <div className="stack swap-picker">
      <div className="pick-step" role="group" aria-label="Swap by">
        <span className="muted">Find by</span>
        {(["person", "chore"] as By[]).map((b) => (
          <button type="button" key={b} className={`chip${by === b ? " on" : ""}`} aria-pressed={by === b}
            onClick={() => { setBy(b); setFirst(null); setSecond(null); onChange(""); }}>{b === "person" ? "Person" : "Chore"}</button>
        ))}
      </div>
      {chips(by === "person" ? "Whose" : "Which chore", firsts, first, (x) => { setFirst(x); setSecond(null); onChange(""); })}
      {first && chips(by === "person" ? "Which chore" : "Whose", seconds, second, (x) => { setSecond(x); onChange(""); })}
      {first && second && <DayPicker days={dates} own={own} value={value} onChange={onChange} />}
    </div>
  );
}

type View = "week" | "month" | "list";

/** The last step: a small calendar where only the days you could swap with
 *  can be picked, and your own day is marked. Week, month or a plain list. */
export function DayPicker({ days, own, value, onChange }: {
  days: Candidate[]; own?: string; value: string; onChange: (key: string) => void;
}) {
  const firstDay = days[0]?.date ?? today();
  const [view, setView] = useState<View>(() => {
    try { return (localStorage.getItem("choreboard.swapView") as View) || "month"; } catch { return "month"; }
  });
  const [anchor, setAnchor] = useState(firstDay);
  const byDate = new Map(days.map((c) => [c.date, c]));
  const pickView = (v: View) => {
    setView(v);
    try { localStorage.setItem("choreboard.swapView", v); } catch { /* fine */ }
  };

  const cell = (d: string, outside = false) => {
    const c = byDate.get(d);
    const cls = ["day", c ? "ok" : "", c && c.key === value ? "on" : "", d === own ? "own" : "", outside ? "outside" : "",
      d === today() ? "today" : ""].filter(Boolean).join(" ");
    const label = `${dayLabel(d)}${c ? (c.window ? `, ${c.window}` : "") : d === own ? ", your day" : ", not available"}`;
    return c
      ? <button type="button" key={d} role="radio" aria-checked={c.key === value} aria-label={label} className={cls}
          onClick={() => onChange(c.key)}>{parse(d).getDate()}</button>
      : <span key={d} className={cls} aria-label={label} title={d === own ? "Your day" : undefined}>{parse(d).getDate()}</span>;
  };
  const weekdays = ["M", "T", "W", "T", "F", "S", "S"];

  let body;
  let title = "";
  let step = (_n: number) => {};
  if (view === "list") {
    body = (
      <div className="pick-step" role="radiogroup" aria-label="Which day">
        {days.map((c) => (
          <button type="button" role="radio" key={c.key} aria-checked={value === c.key}
            className={`chip${value === c.key ? " on" : ""}`} onClick={() => onChange(c.key)}>
            {dayLabel(c.date)}{c.window ? ` · ${c.window}` : ""}
          </button>
        ))}
      </div>
    );
  } else {
    const [from, to] = view === "week" ? [weekStart(anchor), addDays(weekStart(anchor), 6)] : monthGrid(anchor);
    const cells: string[] = [];
    for (let d = from; d <= to; d = addDays(d, 1)) cells.push(d);
    const inMonth = (d: string) => view === "week" || monthStart(d) === monthStart(anchor);
    title = view === "week" ? `Week of ${dayLabel(from)}` : monthLabel(anchor);
    step = (n) => setAnchor(view === "week" ? addDays(anchor, 7 * n) : addMonths(anchor, n));
    body = (
      <div className="mini-cal" role="radiogroup" aria-label="Which day">
        {weekdays.map((w, i) => <span key={`h${i}`} className="dow" aria-hidden>{w}</span>)}
        {cells.map((d) => cell(d, !inMonth(d)))}
      </div>
    );
  }
  return (
    <div className="stack day-picker">
      <div className="pick-step">
        <span className="muted">Which day</span>
        <span className="seg" role="group" aria-label="Calendar view">
          {(["week", "month", "list"] as View[]).map((v) => (
            <button type="button" key={v} aria-pressed={view === v} className={`chip${view === v ? " on" : ""}`}
              onClick={() => pickView(v)}>{v === "list" ? "Agenda" : v[0].toUpperCase() + v.slice(1)}</button>
          ))}
        </span>
      </div>
      {view !== "list" && (
        <div className="mini-nav">
          <button type="button" className="ghost" aria-label={`Previous ${view}`} onClick={() => step(-1)}>‹</button>
          <span>{title}</span>
          <button type="button" className="ghost" aria-label={`Next ${view}`} onClick={() => step(1)}>›</button>
        </div>
      )}
      {body}
      {own && <p className="muted legend"><span className="day own sample" /> your day · <span className="day ok sample" /> can swap</p>}
    </div>
  );
}
