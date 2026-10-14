import { useCallback, useEffect, useMemo, useState } from "react";
import { api, clock, type Chore, type Entry, type Person } from "../api";
import { ErrorNote } from "../components/ErrorNote";
import { OccurrencePanel } from "../components/OccurrencePanel";
import { addDays, dayLabel, today, weekStart } from "../dates";

const STATUS_LABEL: Record<string, string> = {
  done: "Done", skipped: "Not needed", failed: "Missed", covered: "Covered", failed_covered: "Missed · covered",
};

/** A week of the household's calendar: the schedule with every override
 *  applied, and what was recorded. Click anything to act on it. */
export function CalendarPage({ me }: { me: Person }) {
  const [start, setStart] = useState(() => weekStart(today()));
  const [mine, setMine] = useState(false);
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [chores, setChores] = useState<Chore[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [points, setPoints] = useState<Record<number, number>>({});
  const [open, setOpen] = useState<Entry | null>(null);
  const [error, setError] = useState<unknown>(null);
  const end = addDays(start, 6);

  const load = useCallback(async () => {
    try {
      const [e, c, p] = await Promise.all([api.calendar(start, end, mine ? me.id : undefined), api.chores(), api.people()]);
      setEntries(e); setChores(c); setPeople(p);
      const balances = await Promise.all(p.map((x) => api.points(x.id).then((r) => [x.id, r.balance] as const)));
      setPoints(Object.fromEntries(balances));
      setError(null);
    } catch (err) {
      setError(err);
    }
  }, [start, end, mine, me.id]);
  useEffect(() => { load(); }, [load]);

  const chore = (id: number) => chores.find((c) => c.id === id);
  const person = (id: number | null) => people.find((p) => p.id === id);
  const days = useMemo(() => Array.from({ length: 7 }, (_, i) => addDays(start, i)), [start]);

  return (
    <section>
      <div className="calhead">
        <h1>Calendar</h1>
        <div className="actions">
          <button className="ghost" onClick={() => setStart(addDays(start, -7))} aria-label="Previous week">‹</button>
          <button className="ghost" onClick={() => setStart(weekStart(today()))}>This week</button>
          <button className="ghost" onClick={() => setStart(addDays(start, 7))} aria-label="Next week">›</button>
          <label className="inline"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Only mine</label>
        </div>
      </div>

      <ul className="points" aria-label="Points">
        {people.map((p) => (
          <li key={p.id}><span className={`swatch c-${p.color || "none"}`} aria-hidden /> {p.name} <strong>{points[p.id] ?? "…"}</strong></li>
        ))}
      </ul>

      <ErrorNote error={error} />
      {entries && (
        <ol className="days">
          {days.map((d) => {
            const items = entries.filter((e) => e.date === d);
            return (
              <li key={d} className={d === today() ? "day today" : "day"}>
                <h2>{dayLabel(d)}</h2>
                {items.length === 0 ? <p className="muted">Nothing.</p> : (
                  <ul className="list">
                    {items.map((e) => {
                      const c = chore(e.chore_id);
                      const p = person(e.person_id);
                      const moved = e.scheduled_person_id != null;
                      return (
                        <li key={e.id}>
                          <button className={`occ${e.skipped ? " skipped" : ""}${e.outcome ? ` st-${e.outcome.status}` : ""}`}
                            onClick={() => setOpen(e)}>
                            <span className="icon" aria-hidden>{c?.icon || "•"}</span>
                            <span className="grow">
                              <strong>{c?.title ?? `Chore ${e.chore_id}`}</strong>
                              <span className="muted block">
                                <span className={`swatch c-${p?.color || "none"}`} aria-hidden /> {p?.name ?? "?"}
                                {e.due && ` · by ${clock(e.due)}`}
                                {moved && ` · instead of ${person(e.scheduled_person_id)?.name}`}
                                {e.source === "override" && !moved && !e.skipped && e.rule_id == null && " · extra"}
                              </span>
                            </span>
                            {e.skipped && <span className="badge">Skipped</span>}
                            {e.outcome && <span className="badge">{STATUS_LABEL[e.outcome.status]}</span>}
                            {e.group_id && !e.outcome && <span className="badge swap">Swapped</span>}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </li>
            );
          })}
        </ol>
      )}

      {open && (
        <OccurrencePanel entry={open} me={me} chores={chores} people={people}
          onClose={() => setOpen(null)} onChanged={() => { setOpen(null); load(); }} />
      )}
    </section>
  );
}
