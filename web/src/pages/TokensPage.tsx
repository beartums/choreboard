import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, type ApiToken, type Person, type Scope } from "../api";
import { ErrorNote } from "../components/ErrorNote";

const SCOPES: { scope: Scope; label: string; help: string }[] = [
  { scope: "read", label: "Read", help: "see the calendar, chores, people and points" },
  { scope: "write", label: "Write", help: "record outcomes, swap, change schedules" },
  { scope: "act_as", label: "Act as anyone",
    help: "name a person per request (X-Choreboard-As) — what nav needs to act for whoever is looking. Powerful: give it only to services you run." },
];

function ago(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

/** API tokens for other programs (nav, automations). Admins only. A token
 *  is shown once, when it's made; after that only its first characters. */
export function TokensPage({ me }: { me: Person }) {
  const [tokens, setTokens] = useState<ApiToken[] | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [fresh, setFresh] = useState<(ApiToken & { token: string }) | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    Promise.all([api.tokens(), api.people()]).then(([t, p]) => { setTokens(t); setPeople(p); setError(null); }, setError);
  }, []);
  useEffect(load, [load]);

  async function revoke(t: ApiToken) {
    if (!confirm(`Revoke "${t.name}"? Anything using it stops working at once.`)) return;
    try { await api.revokeToken(t.id); load(); } catch (err) { setError(err); }
  }

  const live = (tokens ?? []).filter((t) => !t.revoked_at);
  const revoked = (tokens ?? []).filter((t) => t.revoked_at);

  return (
    <section>
      <Link to="/settings" className="back">← Settings</Link>
      <h1>API tokens</h1>
      <p className="muted">For other programs that use Choreboard — nav's chore calendar, an automation. Send one as
        <code>Authorization: Bearer cb_…</code>. People don't need these; they sign in.</p>
      <ErrorNote error={error} />

      {fresh && (
        <div className="card stack fresh" role="status" aria-label="New token">
          <strong>“{fresh.name}” — copy it now. It won't be shown again.</strong>
          <div className="iconfield">
            <input readOnly value={fresh.token} aria-label="Token" onFocus={(e) => e.target.select()} />
            <button type="button" onClick={() => navigator.clipboard?.writeText(fresh.token)}>Copy</button>
          </div>
          <div className="actions"><button className="ghost" onClick={() => setFresh(null)}>I've saved it</button></div>
        </div>
      )}

      <h2>Make a token</h2>
      <NewToken me={me} people={people} onMade={(t) => { setFresh(t); load(); }} onError={setError} />

      <h2>Tokens</h2>
      {tokens && live.length === 0 && <p className="muted">None yet.</p>}
      {live.length > 0 && (
        <ul className="list" aria-label="Tokens">
          {live.map((t) => (
            <li key={t.id} className="row">
              <span className="grow">
                <strong>{t.name}</strong> <code>{t.prefix}…</code>
                <span className="muted block">{t.scopes.map((s) => SCOPES.find((x) => x.scope === s)?.label ?? s).join(", ")}
                  {" · acts as "}{t.person_name} · made {ago(t.created_at)} · last used {ago(t.last_used)}</span>
              </span>
              <button className="ghost danger" onClick={() => revoke(t)}>Revoke</button>
            </li>
          ))}
        </ul>
      )}
      {revoked.length > 0 && (
        <details>
          <summary className="muted">{revoked.length} revoked</summary>
          <ul className="list">
            {revoked.map((t) => (
              <li key={t.id} className="row muted"><span className="grow"><s>{t.name}</s> <code>{t.prefix}…</code> · revoked {ago(t.revoked_at)}</span></li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function NewToken({ me, people, onMade, onError }: {
  me: Person; people: Person[]; onMade: (t: ApiToken & { token: string }) => void; onError: (e: unknown) => void;
}) {
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<Scope[]>(["read"]);
  const [person, setPerson] = useState<number>(me.id);
  const toggle = (s: Scope) => setScopes(scopes.includes(s) ? scopes.filter((x) => x !== s) : [...scopes, s]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      onMade(await api.createToken(name.trim(), scopes, person));
      setName(""); setScopes(["read"]); setPerson(me.id);
    } catch (err) {
      onError(err);
    }
  }

  return (
    <form className="card stack" onSubmit={submit} aria-label="Make a token">
      <div className="grid2">
        <label>Name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="nav" required /></label>
        <label>Acts as
          <select value={person} onChange={(e) => setPerson(Number(e.target.value))}>
            {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
      </div>
      <fieldset className="stack">
        <legend>Can</legend>
        {SCOPES.map((s) => (
          <label key={s.scope} className="inline check">
            <input type="checkbox" checked={scopes.includes(s.scope)} onChange={() => toggle(s.scope)} aria-label={s.label} />
            <span><strong>{s.label}</strong> <span className="muted">— {s.help}</span></span>
          </label>
        ))}
      </fieldset>
      <p className="muted">For nav's chore calendar, <strong>Read</strong> is enough.</p>
      <div className="actions"><button type="submit" disabled={!name.trim() || scopes.length === 0}>Make token</button></div>
    </form>
  );
}
