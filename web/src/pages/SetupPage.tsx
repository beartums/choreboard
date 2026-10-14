import { useState, type FormEvent } from "react";
import { api } from "../api";
import { ErrorNote } from "../components/ErrorNote";

/** First visit: nobody exists yet. The signed-in person becomes the first admin. */
export function SetupPage({ email, onDone }: { email: string; onDone: () => void }) {
  const [name, setName] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.setup(name.trim());
      onDone();
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <main className="page narrow">
      <h1>Set up Choreboard</h1>
      <p>You're the first one here, signed in as <strong>{email}</strong>. You'll be the household's first admin; everyone else joins by signing in.</p>
      <form onSubmit={submit} className="stack">
        <label>
          Your name
          <input value={name} onChange={(e) => setName(e.target.value)} required autoFocus />
        </label>
        <ErrorNote error={error} />
        <button type="submit" disabled={busy || !name.trim()}>Start</button>
      </form>
    </main>
  );
}
