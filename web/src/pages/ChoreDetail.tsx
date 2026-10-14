import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, describeRule, timeWindow, WEEKDAYS, type Chore, type Person, type Rule, type RuleInput } from "../api";
import { ErrorNote } from "../components/ErrorNote";
import { IconPicker } from "../components/IconPicker";
import { useIsAdmin } from "../me";
import { isRotationRule, overlaps, planRotation, rotationOf, rotations, type Rotation, type RotationGroup } from "../rotation";
import { addDays } from "../dates";

/** One chore: its details, its weekly rotation, and any other rules. */
export function ChoreDetail() {
  const id = Number(useParams().id);
  const [chore, setChore] = useState<Chore | null>(null);
  const [rules, setRules] = useState<Rule[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [adding, setAdding] = useState(false);

  const load = useCallback(() => {
    Promise.all([api.chores(), api.rules(id), api.people()]).then(([cs, rs, ps]) => {
      setChore(cs.find((c) => c.id === id) ?? null);
      setRules(rs); setPeople(ps);
      if (!cs.find((c) => c.id === id)) setError(new Error("That chore doesn't exist (or was retired)."));
    }, setError);
  }, [id]);
  useEffect(load, [load]);

  if (error) return <section><ErrorNote error={error} /><Link to="/chores">← Chores</Link></section>;
  if (!chore) return <p className="muted">Loading…</p>;
  const others = rules.filter((r) => !isRotationRule(r));
  const groups = rotations(rules);
  const admin = useIsAdmin();

  if (!admin) {
    // Members see the schedule; changing it is an admin's job.
    const who = (id: number) => people.find((p) => p.id === id)?.name ?? `#${id}`;
    return (
      <section>
        <Link to="/chores" className="back">← Chores</Link>
        <h1>{chore.icon} {chore.title}</h1>
        <p className="muted">{chore.points} point{chore.points === 1 ? "" : "s"}{chore.description && ` · ${chore.description}`}</p>
        <ul className="list" aria-label="Schedule">
          {rules.map((r) => <li key={r.id} className="row"><strong>{who(r.person_id)}</strong> <span className="muted">{describeRule(r)}</span></li>)}
        </ul>
        {rules.length === 0 && <p className="muted">Not scheduled.</p>}
      </section>
    );
  }

  return (
    <section>
      <Link to="/chores" className="back">← Chores</Link>
      <ChoreForm chore={chore} onSaved={load} />
      <h2>Weekly rotation</h2>
      <p className="muted">Who does it on each day of the week. Leave a day empty if nobody does. Give a rotation a
        start date to have it take over from then; add another for a different stretch (summer, a new term).</p>
      {groups.map((g) => <RotationEditor key={g.key} chore={chore} group={g} people={people} onSaved={load}
        others={groups.filter((o) => o.key !== g.key)} />)}
      {(groups.length === 0 || adding) && (
        <RotationEditor chore={chore} group={{ key: "new", start: null, end: null, rules: [] }} people={people}
          onSaved={() => { setAdding(false); load(); }} isNew={groups.length > 0} onCancel={() => setAdding(false)}
          others={groups} />
      )}
      {groups.length > 0 && !adding && (
        <div className="actions"><button className="ghost" onClick={() => setAdding(true)}>Add another rotation</button></div>
      )}
      <h2>Other schedules</h2>
      <p className="muted">Every few days, or a single date.</p>
      {others.length > 0 && (
        <ul className="list" aria-label="Other schedules">
          {others.map((r) => <RuleRow key={r.id} rule={r} people={people} onChanged={load} />)}
        </ul>
      )}
      <RuleForm choreId={chore.id} people={people} onSaved={load} />
    </section>
  );
}

function ChoreForm({ chore, onSaved }: { chore: Chore; onSaved: () => void }) {
  const navigate = useNavigate();
  const [title, setTitle] = useState(chore.title);
  const [icon, setIcon] = useState(chore.icon);
  const [points, setPoints] = useState(chore.points);
  const [description, setDescription] = useState(chore.description);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null); setSaved(false);
    try {
      await api.updateChore(chore.id, { title: title.trim(), icon: icon.trim(), points, description });
      setSaved(true);
      onSaved();
    } catch (err) {
      setError(err);
    }
  }

  async function retire() {
    if (!confirm(`Retire "${chore.title}"? It stops appearing; its history and points are kept.`)) return;
    try {
      await api.updateChore(chore.id, { active: false });
      navigate("/chores");
    } catch (err) {
      setError(err);
    }
  }

  return (
    <form onSubmit={submit} className="card stack" aria-label="Chore details">
      <div className="grid3">
        <label>Chore<input value={title} onChange={(e) => setTitle(e.target.value)} required /></label>
        <IconPicker value={icon} onChange={setIcon} />
        <label>Points<input type="number" min={0} value={points} onChange={(e) => setPoints(Number(e.target.value))} /></label>
      </div>
      <label>Notes<textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} /></label>
      <ErrorNote error={error} />
      <div className="actions">
        <button type="submit">Save</button>
        {saved && <span className="muted" role="status">Saved. Points already earned don't change.</span>}
        <button type="button" className="danger ghost push" onClick={retire}>Retire chore</button>
      </div>
    </form>
  );
}

function RotationEditor({ chore, group, people, onSaved, isNew, onCancel, others = [] }: {
  chore: Chore; group: RotationGroup; people: Person[]; onSaved: () => void; isNew?: boolean; onCancel?: () => void;
  others?: RotationGroup[];
}) {
  const initial = rotationOf(group.rules);
  const initialDue = group.rules[0]?.due ?? "";
  const initialFrom = group.rules[0]?.available ?? "";
  const [want, setWant] = useState<Rotation>(initial);
  const [due, setDue] = useState(initialDue);
  const [from, setFrom] = useState(initialFrom);
  const [start, setStart] = useState(group.start ?? "");
  const [end, setEnd] = useState(group.end ?? "");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setWant(rotationOf(group.rules)); setDue(group.rules[0]?.due ?? ""); setFrom(group.rules[0]?.available ?? "");
    setStart(group.start ?? ""); setEnd(group.end ?? "");
  }, [group]);

  const plan = planRotation(chore.id, group.rules, want, due || null, start || null, end || null, from || null);
  const dirty = plan.create.length + plan.update.length + plan.remove.length > 0;
  const badRange = !!start && !!end && end < start;
  const badWindow = !!from && !!due && from >= due;

  const clash = others.filter((o) => overlaps(o, { start: start || null, end: end || null }) && Object.keys(want).length > 0);

  /** End an earlier, open-ended rotation the day before this one starts, so
   *  this one takes over instead of doubling up. */
  async function endOther(o: RotationGroup) {
    setBusy(true); setError(null);
    try {
      for (const r of o.rules) await api.updateRule(r.id, { ...r, end: addDays(start, -1) });
      onSaved();
    } catch (err) {
      setError(err);
    }
    setBusy(false);
  }

  async function save() {
    setBusy(true); setError(null);
    try {
      for (const u of plan.update) await api.updateRule(u.id, u.rule);
      for (const c of plan.create) await api.createRule(c);
      for (const r of plan.remove) await api.deleteRule(r);
      onSaved();
    } catch (err) {
      setError(err);
    }
    setBusy(false);
  }

  const title = group.start || group.end
    ? `Rotation ${group.start ? `from ${group.start}` : ""}${group.end ? ` until ${group.end}` : ""}`.trim()
    : isNew ? "New rotation" : "Weekly rotation";

  return (
    <div className="card stack rotation" aria-label={title}>
      <div className="week">
        {WEEKDAYS.map(({ iso, short }) => (
          <label key={iso} className="day">
            {short}
            <select aria-label={short} value={want[iso] ?? ""}
              onChange={(e) => setWant({ ...want, [iso]: e.target.value ? Number(e.target.value) : undefined })}>
              <option value="">—</option>
              {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
        ))}
      </div>
      <fieldset className="grid2">
        <legend>Each day</legend>
        <label>Start time<input type="time" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label>Due by<input type="time" value={due} onChange={(e) => setDue(e.target.value)} /></label>
      </fieldset>
      <fieldset className="grid2">
        <legend>Dates</legend>
        <label>First day<input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label>Last day<input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
      </fieldset>
      <p className="muted">{timeWindow(from || null, due || null) || "Any time of day"} · {start ? `from ${start}`
        : group.rules[0]?.first_day ? `since ${group.rules[0].first_day}, when it was set up` : "from today"}{end ? `, last day ${end}` : ", no end date"}</p>
      {badRange && <p className="error" role="alert">The last day is before the first.</p>}
      {badWindow && <p className="error" role="alert">The start time is after the due time.</p>}
      {clash.map((o) => (
        <p key={o.key} className="note" role="status">
          Overlaps another rotation{o.start ? ` (from ${o.start})` : ""}: on shared days both apply.
          {start && !o.end && (o.start ?? "") < start && (
            <> <button className="ghost" type="button" onClick={() => endOther(o)} disabled={busy}>
              End that one on {addDays(start, -1)}</button></>
          )}
        </p>
      ))}
      <ErrorNote error={error} />
      <div className="actions">
        <button onClick={save} disabled={!dirty || busy || badRange || badWindow}>Save rotation</button>
        {dirty && !isNew && <button className="ghost" onClick={() => {
          setWant(initial); setDue(initialDue); setFrom(initialFrom); setStart(group.start ?? ""); setEnd(group.end ?? "");
        }}>Reset</button>}
        {onCancel && isNew && <button className="ghost" onClick={onCancel}>Cancel</button>}
      </div>
    </div>
  );
}

function RuleRow({ rule, people, onChanged }: { rule: Rule; people: Person[]; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const who = people.find((p) => p.id === rule.person_id)?.name ?? `#${rule.person_id}`;

  async function remove() {
    if (!confirm(`Delete this schedule for ${who}? What's already been recorded is kept.`)) return;
    try { await api.deleteRule(rule.id); onChanged(); } catch (err) { setError(err); }
  }

  if (editing) {
    return <li><RuleForm choreId={rule.chore_id} people={people} initial={rule}
      onSaved={() => { setEditing(false); onChanged(); }} onCancel={() => setEditing(false)} /></li>;
  }
  return (
    <li className="row">
      <span className="grow"><strong>{who}</strong> <span className="muted">{describeRule(rule)}</span></span>
      <ErrorNote error={error} />
      <button className="ghost" onClick={() => setEditing(true)}>Edit</button>
      <button className="ghost danger" onClick={remove}>Delete</button>
    </li>
  );
}

// Weekday schedules are rotations (above); this form is for the rest.
type Kind = "every" | "once";

export function RuleForm({ choreId, people, initial, onSaved, onCancel }:
  { choreId: number; people: Person[]; initial?: Rule; onSaved: () => void; onCancel?: () => void }) {
  const today = new Date().toISOString().slice(0, 10);
  const [person, setPerson] = useState<number | "">(initial?.person_id ?? people[0]?.id ?? "");
  const [kind, setKind] = useState<Kind>(initial?.on ? "once" : "every");
  const [every, setEvery] = useState(initial?.every_days ?? 2);
  const [anchor, setAnchor] = useState(initial?.anchor ?? today);
  const [on, setOn] = useState(initial?.on ?? today);
  const [due, setDue] = useState(initial?.due ?? "");
  const [from, setFrom] = useState(initial?.available ?? "");
  const [start, setStart] = useState(initial?.start ?? "");
  const [end, setEnd] = useState(initial?.end ?? "");
  const [error, setError] = useState<unknown>(null);

  const badWindow = !!from && !!due && from >= due;
  const valid = person !== "" && (kind !== "every" || every >= 1) && !badWindow;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (person === "") return;
    setError(null);
    const rule: RuleInput = {
      chore_id: choreId, person_id: person,
      weekdays: null,
      every_days: kind === "every" ? every : null, anchor: kind === "every" ? anchor : null,
      on: kind === "once" ? on : null,
      available: from || null,
      due: due || null,
      start: kind === "once" ? null : start || null, end: kind === "once" ? null : end || null,
    };
    try {
      if (initial) await api.updateRule(initial.id, rule);
      else await api.createRule(rule);
      onSaved();
    } catch (err) {
      setError(err);
    }
  }

  return (
    <form onSubmit={submit} className="card stack" aria-label={initial ? "Edit schedule" : "Add a schedule"}>
      <div className="grid2">
        <label>Who
          <select value={person} onChange={(e) => setPerson(Number(e.target.value))} required>
            {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <label>Repeats
          <select value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
            <option value="every">every few days</option>
            <option value="once">once</option>
          </select>
        </label>
      </div>
      {kind === "every" && (
        <div className="grid2">
          <label>Every (days)<input type="number" min={1} value={every} onChange={(e) => setEvery(Number(e.target.value))} /></label>
          <label>Starting<input type="date" value={anchor} onChange={(e) => setAnchor(e.target.value)} required /></label>
        </div>
      )}
      {kind === "once" && <label>On<input type="date" value={on} onChange={(e) => setOn(e.target.value)} required /></label>}
      <fieldset className="grid2">
        <legend>Each day</legend>
        <label>Start time<input type="time" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label>Due by<input type="time" value={due} onChange={(e) => setDue(e.target.value)} /></label>
      </fieldset>
      {kind !== "once" && (
        <fieldset className="grid2">
          <legend>Dates</legend>
          <label>First day<input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
          <label>Last day<input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
        </fieldset>
      )}
      {badWindow && <p className="error" role="alert">The start time is after the due time.</p>}
      <ErrorNote error={error} />
      <div className="actions">
        <button type="submit" disabled={!valid}>{initial ? "Save" : "Add schedule"}</button>
        {onCancel && <button type="button" className="ghost" onClick={onCancel}>Cancel</button>}
      </div>
    </form>
  );
}
