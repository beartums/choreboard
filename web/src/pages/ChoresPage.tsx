import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, describeRule, type Chore, type Person, type Rule } from "../api";
import { ErrorNote } from "../components/ErrorNote";

/** Every chore, with who does it when -- the household's default week. */
export function ChoresPage() {
  const [chores, setChores] = useState<Chore[] | null>(null);
  const [rules, setRules] = useState<Rule[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    Promise.all([api.chores(), api.rules(), api.people()]).then(([c, r, p]) => {
      setChores(c); setRules(r); setPeople(p);
    }, setError);
  }, []);
  useEffect(load, [load]);

  const name = (id: number) => people.find((p) => p.id === id)?.name ?? `#${id}`;

  return (
    <section>
      <h1>Chores</h1>
      <ErrorNote error={error} />
      {chores && chores.length === 0 && <p className="muted">No chores yet. Add the first one below.</p>}
      {chores && (
        <ul className="list" aria-label="Chores">
          {chores.map((c) => {
            const mine = rules.filter((r) => r.chore_id === c.id);
            return (
              <li key={c.id}>
                <Link to={`/chores/${c.id}`} className="row link">
                  <span className="icon" aria-hidden>{c.icon || "•"}</span>
                  <span className="grow">
                    <strong>{c.title}</strong> <span className="muted">{c.points} pt{c.points === 1 ? "" : "s"}</span>
                    {mine.length === 0
                      ? <span className="muted block">Not scheduled</span>
                      : mine.map((r) => <span key={r.id} className="muted block">{name(r.person_id)}: {describeRule(r)}</span>)}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      <h2>Add a chore</h2>
      <NewChore />
    </section>
  );
}

function NewChore() {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [icon, setIcon] = useState("");
  const [points, setPoints] = useState(1);
  const [error, setError] = useState<unknown>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const c = await api.createChore({ title: title.trim(), icon: icon.trim(), points });
      navigate(`/chores/${c.id}`);            // straight on to scheduling it
    } catch (err) {
      setError(err);
    }
  }

  return (
    <form onSubmit={submit} className="card stack" aria-label="Add a chore">
      <div className="grid3">
        <label>Chore<input value={title} onChange={(e) => setTitle(e.target.value)} required placeholder="Walk the dog" /></label>
        <label>Icon<input value={icon} onChange={(e) => setIcon(e.target.value)} placeholder="🐕" maxLength={4} /></label>
        <label>Points<input type="number" min={0} value={points} onChange={(e) => setPoints(Number(e.target.value))} /></label>
      </div>
      <ErrorNote error={error} />
      <div className="actions"><button type="submit" disabled={!title.trim()}>Add and schedule</button></div>
    </form>
  );
}
