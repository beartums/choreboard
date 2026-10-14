import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, type Person } from "../api";
import { ErrorNote } from "../components/ErrorNote";

export const COLORS = ["", "coral", "mint", "butter", "sky", "rose", "leaf", "lilac", "sand"];

/** Everyone in the household. People also join by signing in; adding them
 *  here first (with the email they sign in with) lets you give them chores
 *  before their first visit. */
export function PeoplePage() {
  const [people, setPeople] = useState<Person[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [editing, setEditing] = useState<number | null>(null);

  const load = useCallback(() => {
    api.people().then(setPeople, setError);
  }, []);
  useEffect(load, [load]);

  return (
    <section>
      <h1>People</h1>
      <ErrorNote error={error} />
      {people && (
        <ul className="list" aria-label="People">
          {people.map((p) =>
            editing === p.id ? (
              <li key={p.id}>
                <PersonForm initial={p} onSaved={() => { setEditing(null); load(); }} onCancel={() => setEditing(null)} />
              </li>
            ) : (
              <li key={p.id} className="row">
                <span className={`swatch c-${p.color || "none"}`} aria-hidden />
                <span className="grow">
                  <strong>{p.name}</strong>
                  {p.role === "admin" && <span className="tag">admin</span>}
                  <span className="muted block">{p.email ?? "no sign-in email yet"}</span>
                </span>
                <button className="ghost" onClick={() => setEditing(p.id)}>Edit</button>
              </li>
            ),
          )}
        </ul>
      )}
      <h2>Add someone</h2>
      <PersonForm onSaved={load} />
    </section>
  );
}

function PersonForm({ initial, onSaved, onCancel }: { initial?: Person; onSaved: () => void; onCancel?: () => void }) {
  const [name, setName] = useState(initial?.name ?? "");
  const [email, setEmail] = useState(initial?.email ?? "");
  const [color, setColor] = useState(initial?.color ?? "");
  const [role, setRole] = useState<Person["role"]>(initial?.role ?? "member");
  const [error, setError] = useState<unknown>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const body = { name: name.trim(), email: email.trim() || undefined, color, role };
    try {
      if (initial) await api.updatePerson(initial.id, body);
      else {
        await api.createPerson(body);
        setName(""); setEmail(""); setColor(""); setRole("member");
      }
      onSaved();
    } catch (err) {
      setError(err);
    }
  }

  async function deactivate() {
    if (!initial || !confirm(`Remove ${initial.name}? Their history and points are kept.`)) return;
    try {
      await api.updatePerson(initial.id, { active: false });
      onSaved();
    } catch (err) {
      setError(err);
    }
  }

  return (
    <form onSubmit={submit} className="card stack" aria-label={initial ? `Edit ${initial.name}` : "Add someone"}>
      <div className="grid2">
        <label>Name<input value={name} onChange={(e) => setName(e.target.value)} required /></label>
        <label>Sign-in email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="optional" /></label>
        <label>Colour
          <select value={color} onChange={(e) => setColor(e.target.value)}>
            {COLORS.map((c) => <option key={c} value={c}>{c || "none"}</option>)}
          </select>
        </label>
        <label>Role
          <select value={role} onChange={(e) => setRole(e.target.value as Person["role"])}>
            <option value="member">member</option>
            <option value="admin">admin</option>
          </select>
        </label>
      </div>
      <ErrorNote error={error} />
      <div className="actions">
        <button type="submit" disabled={!name.trim()}>{initial ? "Save" : "Add"}</button>
        {onCancel && <button type="button" className="ghost" onClick={onCancel}>Cancel</button>}
        {initial && <button type="button" className="danger ghost push" onClick={deactivate}>Remove</button>}
      </div>
    </form>
  );
}
