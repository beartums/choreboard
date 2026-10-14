import { useCallback, useEffect, useMemo, useState } from "react";
import { api, timeWindow, type Chore, type Entry, type Person } from "../api";
import { ErrorNote } from "../components/ErrorNote";
import { OccurrencePanel } from "../components/OccurrencePanel";
import { addDays, addMonths, dayLabel, monthGrid, monthLabel, parse, today, weekStart } from "../dates";

export type View = "agenda" | "week" | "month";

const STATUS_LABEL: Record<string, string> = {
  done: "Done", skipped: "Not needed", failed: "Missed", covered: "Covered", failed_covered: "Missed · covered",
};

function savedView(): View {
  try {
    const v = localStorage.getItem("choreboard.view");
    return v === "week" || v === "month" ? v : "agenda";
  } catch {
    return "agenda";
  }
}

/** The household's calendar: the schedule with every override applied, and
 *  what was recorded -- as an agenda, a week grid or a month grid. Click
 *  anything to act on it. */
export function CalendarPage({ me }: { me: Person }) {
  const [view, setViewState] = useState<View>(savedView);
  const [anchor, setAnchor] = useState(today);          // any day in the period shown
  const [mine, setMine] = useState(false);
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [chores, setChores] = useState<Chore[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [points, setPoints] = useState<Record<number, number>>({});
  const [open, setOpen] = useState<Entry | null>(null);
  const [error, setError] = useState<unknown>(null);

  const setView = (v: View) => {
    setViewState(v);
    try { localStorage.setItem("choreboard.view", v); } catch { /* private mode: fine */ }
  };
  const [from, to] = view === "month" ? monthGrid(anchor) : [weekStart(anchor), addDays(weekStart(anchor), 6)];

  const load = useCallback(async () => {
    try {
      const [e, c, p] = await Promise.all([api.calendar(from, to, mine ? me.id : undefined), api.chores(), api.people()]);
      setEntries(e); setChores(c); setPeople(p);
      const balances = await Promise.all(p.map((x) => api.points(x.id).then((r) => [x.id, r.balance] as const)));
      setPoints(Object.fromEntries(balances));
      setError(null);
    } catch (err) {
      setError(err);
    }
  }, [from, to, mine, me.id]);
  useEffect(() => { load(); }, [load]);

  const step = (n: number) => setAnchor(view === "month" ? addMonths(anchor, n) : addDays(anchor, 7 * n));
  const days = useMemo(() => {
    const out: string[] = [];
    for (let d = from; d <= to; d = addDays(d, 1)) out.push(d);
    return out;
  }, [from, to]);
  const byDay = useMemo(() => {
    const m = new Map<string, Entry[]>();
    for (const e of entries ?? []) m.set(e.date, [...(m.get(e.date) ?? []), e]);
    return m;
  }, [entries]);

  const ctx: ItemCtx = {
    chore: (id) => chores.find((c) => c.id === id),
    person: (id) => people.find((p) => p.id === id),
    open: setOpen,
  };
  const title = view === "month" ? monthLabel(anchor)
    : `${parse(from).toLocaleDateString(undefined, { month: "short", day: "numeric" })} – ${parse(to).toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;

  return (
    <section className={view === "agenda" ? "" : "wide"}>
      <div className="calhead">
        <h1>Calendar <span className="muted period">{title}</span></h1>
        <div className="actions">
          <div className="seg" role="group" aria-label="View">
            {(["agenda", "week", "month"] as View[]).map((v) => (
              <button key={v} aria-pressed={view === v} onClick={() => setView(v)}>{v[0].toUpperCase() + v.slice(1)}</button>
            ))}
          </div>
          <button className="ghost" onClick={() => step(-1)} aria-label={view === "month" ? "Previous month" : "Previous week"}>‹</button>
          <button className="ghost" onClick={() => setAnchor(today())}>Today</button>
          <button className="ghost" onClick={() => step(1)} aria-label={view === "month" ? "Next month" : "Next week"}>›</button>
          <label className="inline"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Only mine</label>
        </div>
      </div>

      <ul className="points" aria-label="Points">
        {people.map((p) => (
          <li key={p.id}><span className={`swatch c-${p.color || "none"}`} aria-hidden /> {p.name} <strong>{points[p.id] ?? "…"}</strong></li>
        ))}
      </ul>

      <ErrorNote error={error} />
      {entries && view === "agenda" && (
        <ol className="days">
          {days.map((d) => (
            <li key={d} className={d === today() ? "day today" : "day"}>
              <h2>{dayLabel(d)}</h2>
              {(byDay.get(d) ?? []).length === 0 ? <p className="muted">Nothing.</p> : (
                <ul className="list">{(byDay.get(d) ?? []).map((e) => <li key={e.id}><Item e={e} ctx={ctx} /></li>)}</ul>
              )}
            </li>
          ))}
        </ol>
      )}
      {entries && view !== "agenda" && (
        <div className={`grid ${view}`} role="grid" aria-label={view === "month" ? "Month" : "Week"}>
          {days.slice(0, 7).map((d) => <div key={d} className="dow" role="columnheader">
            {parse(d).toLocaleDateString(undefined, { weekday: "short" })}</div>)}
          {days.map((d) => (
            <div key={d} role="gridcell" aria-label={dayLabel(d)}
              className={`cell${d === today() ? " today" : ""}${view === "month" && d.slice(0, 7) !== anchor.slice(0, 7) ? " out" : ""}`}>
              <div className="num">{view === "month" ? parse(d).getDate() : parse(d).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</div>
              {(byDay.get(d) ?? []).map((e) => <Item key={e.id} e={e} ctx={ctx} compact />)}
            </div>
          ))}
        </div>
      )}

      {open && (
        <OccurrencePanel entry={open} me={me} chores={chores} people={people}
          onClose={() => setOpen(null)} onChanged={() => { setOpen(null); load(); }} />
      )}
    </section>
  );
}

interface ItemCtx {
  chore: (id: number) => Chore | undefined;
  person: (id: number | null) => Person | undefined;
  open: (e: Entry) => void;
}

/** One occurrence. Full in the agenda; a chip (icon, person, state) in the grids. */
function Item({ e, ctx, compact }: { e: Entry; ctx: ItemCtx; compact?: boolean }) {
  const c = ctx.chore(e.chore_id);
  const p = ctx.person(e.person_id);
  const moved = e.scheduled_person_id != null;
  const cls = `occ${compact ? " chip" : ""}${e.skipped ? " skipped" : ""}${e.outcome ? ` st-${e.outcome.status}` : ""}`;
  const state = e.skipped ? "Skipped" : e.outcome ? STATUS_LABEL[e.outcome.status] : e.group_id ? "Swapped" : "";
  if (compact) {
    return (
      <button className={cls} onClick={() => ctx.open(e)}
        aria-label={`${c?.title ?? "Chore"}, ${p?.name ?? "?"}${state ? `, ${state}` : ""}`}
        title={`${c?.title ?? "Chore"} — ${p?.name}${timeWindow(e.available, e.due) ? `, ${timeWindow(e.available, e.due)}` : ""}${state ? ` (${state})` : ""}`}>
        <span aria-hidden>{c?.icon || "•"}</span>
        <span className={`swatch c-${p?.color || "none"}`} aria-hidden />
        <span className="chipname">{p?.name}</span>
        {state && <span className="chipstate" aria-hidden>{e.outcome ? "✓" : e.skipped ? "–" : "⇄"}</span>}
      </button>
    );
  }
  return (
    <button className={cls} onClick={() => ctx.open(e)}>
      <span className="icon" aria-hidden>{c?.icon || "•"}</span>
      <span className="grow">
        <strong>{c?.title ?? `Chore ${e.chore_id}`}</strong>
        <span className="muted block">
          <span className={`swatch c-${p?.color || "none"}`} aria-hidden /> {p?.name ?? "?"}
          {timeWindow(e.available, e.due) && ` · ${timeWindow(e.available, e.due)}`}
          {moved && ` · instead of ${ctx.person(e.scheduled_person_id)?.name}`}
          {e.source === "override" && !moved && !e.skipped && e.rule_id == null && " · extra"}
          {e.unscheduled && " · no longer scheduled"}
        </span>
      </span>
      {state && <span className={`badge${e.group_id && !e.outcome && !e.skipped ? " swap" : ""}`}>{state}</span>}
    </button>
  );
}
