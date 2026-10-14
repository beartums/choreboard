import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, describeRule, WEEKDAYS, type Chore, type Person, type Rule, type RuleInput, type Weekday } from "../api";
import { ErrorNote } from "../components/ErrorNote";
import { isRotationRule, planRotation, rotationOf, type Rotation } from "../rotation";

/** One chore: its details, its weekly rotation, and any other rules. */
export function ChoreDetail() {
  const id = Number(useParams().id);
  const [chore, setChore] = useState<Chore | null>(null);
  const [rules, setRules] = useState<Rule[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [error, setError] = useState<unknown>(null);

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

  return (
    <section>
      <Link to="/chores" className="back">← Chores</Link>
      <ChoreForm chore={chore} onSaved={load} />
      <h2>Weekly rotation</h2>
      <p className="muted">Who does it on each day of the week. Leave a day empty if nobody does.</p>
      <RotationEditor chore={chore} rules={rules} people={people} onSaved={load} />
      <h2>Other schedules</h2>
      <p className="muted">Every few days, a single date, or a rotation that only runs between two dates.</p>
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
        <label>Icon<input value={icon} onChange={(e) => setIcon(e.target.value)} maxLength={4} /></label>
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

function RotationEditor({ chore, rules, people, onSaved }:
  { chore: Chore; rules: Rule[]; people: Person[]; onSaved: () => void }) {
  const initial = rotationOf(rules);
  const initialDue = rules.find(isRotationRule)?.due ?? "";
  const [want, setWant] = useState<Rotation>(initial);
  const [due, setDue] = useState(initialDue);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { setWant(rotationOf(rules)); setDue(rules.find(isRotationRule)?.due ?? ""); }, [rules]);

  const plan = planRotation(chore.id, rules, want, due || null);
  const dirty = plan.create.length + plan.update.length + plan.remove.length > 0;

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

  return (
    <div className="card stack" aria-label="Weekly rotation">
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
      <label className="inline">Done by <input type="time" value={due} onChange={(e) => setDue(e.target.value)} />
        <span className="muted">optional</span></label>
      <ErrorNote error={error} />
      <div className="actions">
        <button onClick={save} disabled={!dirty || busy}>Save rotation</button>
        {dirty && <button className="ghost" onClick={() => { setWant(initial); setDue(initialDue); }}>Reset</button>}
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

type Kind = "weekdays" | "every" | "once";

export function RuleForm({ choreId, people, initial, onSaved, onCancel }:
  { choreId: number; people: Person[]; initial?: Rule; onSaved: () => void; onCancel?: () => void }) {
  const today = new Date().toISOString().slice(0, 10);
  const [person, setPerson] = useState<number | "">(initial?.person_id ?? people[0]?.id ?? "");
  const [kind, setKind] = useState<Kind>(initial ? (initial.every_days ? "every" : initial.on ? "once" : "weekdays") : "every");
  const [weekdays, setWeekdays] = useState<Weekday[]>(initial?.weekdays ?? []);
  const [every, setEvery] = useState(initial?.every_days ?? 2);
  const [anchor, setAnchor] = useState(initial?.anchor ?? today);
  const [on, setOn] = useState(initial?.on ?? today);
  const [due, setDue] = useState(initial?.due ?? "");
  const [start, setStart] = useState(initial?.start ?? "");
  const [end, setEnd] = useState(initial?.end ?? "");
  const [error, setError] = useState<unknown>(null);

  function toggle(d: Weekday) {
    setWeekdays(weekdays.includes(d) ? weekdays.filter((x) => x !== d) : [...weekdays, d].sort());
  }

  const valid = person !== "" && (kind !== "weekdays" || weekdays.length > 0) && (kind !== "every" || every >= 1);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (person === "") return;
    setError(null);
    const rule: RuleInput = {
      chore_id: choreId, person_id: person,
      weekdays: kind === "weekdays" ? weekdays : null,
      every_days: kind === "every" ? every : null, anchor: kind === "every" ? anchor : null,
      on: kind === "once" ? on : null,
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
            <option value="weekdays">on weekdays</option>
            <option value="once">once</option>
          </select>
        </label>
      </div>
      {kind === "weekdays" && (
        <div className="chips" role="group" aria-label="Days">
          {WEEKDAYS.map(({ iso, short }) => (
            <button type="button" key={iso} aria-pressed={weekdays.includes(iso)} onClick={() => toggle(iso)}>{short}</button>
          ))}
        </div>
      )}
      {kind === "every" && (
        <div className="grid2">
          <label>Every (days)<input type="number" min={1} value={every} onChange={(e) => setEvery(Number(e.target.value))} /></label>
          <label>Starting<input type="date" value={anchor} onChange={(e) => setAnchor(e.target.value)} required /></label>
        </div>
      )}
      {kind === "once" && <label>On<input type="date" value={on} onChange={(e) => setOn(e.target.value)} required /></label>}
      <div className="grid3">
        <label>Done by<input type="time" value={due} onChange={(e) => setDue(e.target.value)} /></label>
        {kind !== "once" && <label>From<input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>}
        {kind !== "once" && <label>Until<input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>}
      </div>
      <ErrorNote error={error} />
      <div className="actions">
        <button type="submit" disabled={!valid}>{initial ? "Save" : "Add schedule"}</button>
        {onCancel && <button type="button" className="ghost" onClick={onCancel}>Cancel</button>}
      </div>
    </form>
  );
}
