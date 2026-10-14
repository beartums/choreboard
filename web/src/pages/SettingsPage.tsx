import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, type ServerSettings, type SettingField } from "../api";
import { ErrorNote } from "../components/ErrorNote";

const GROUPS: { title: string; prefix: string; channel: "email" | "discord"; intro: string }[] = [
  { title: "Email", prefix: "smtp.", channel: "email",
    intro: "Sent over SMTP. With Gmail: turn on 2-Step Verification for the sending account, then create an app password." },
  { title: "Discord", prefix: "discord.", channel: "discord",
    intro: "A bot DMs people. Create an application at discord.com/developers, add a Bot, copy its token, then invite it to your family server (OAuth2 → URL Generator → scope “bot”)." },
];

/** Server settings, for admins: the notification channels' credentials.
 *  Secrets are stored encrypted and never shown again -- only whether
 *  they're set. Anything set by the server's environment is read-only here. */
export function SettingsPage() {
  const [s, setS] = useState<ServerSettings | null>(null);
  const [error, setError] = useState<unknown>(null);
  const load = useCallback(() => { api.settings().then(setS, setError); }, []);
  useEffect(load, [load]);

  if (!s) return <section><h1>Settings</h1><ErrorNote error={error} /></section>;
  return (
    <section>
      <h1>Settings</h1>
      <p className="muted">Only admins see this page.</p>
      {!s.can_store_secrets && (
        <p className="note">Passwords and tokens can't be stored yet: the server needs <code>{s.key_env}</code> (at least 32
          characters) to encrypt them. Until then you can set everything else, or the server can provide them itself.</p>
      )}
      <ErrorNote error={error} />
      {GROUPS.map((g) => (
        <ChannelSettings key={g.prefix} title={g.title} intro={g.intro} live={s.configured.includes(g.channel)}
          canStore={s.can_store_secrets} fields={s.fields.filter((f) => f.key.startsWith(g.prefix))}
          onSaved={setS} />
      ))}
    </section>
  );
}

function ChannelSettings({ title, intro, live, canStore, fields, onSaved }: {
  title: string; intro: string; live: boolean; canStore: boolean; fields: SettingField[]; onSaved: (s: ServerSettings) => void;
}) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(null);
  const [note, setNote] = useState("");
  const dirty = Object.keys(draft).length > 0;

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(null); setNote("");
    try {
      onSaved(await api.saveSettings(draft));
      setDraft({});
      setNote("Saved. It's used from the next notification on.");
    } catch (err) {
      setError(err);
    }
  }
  async function clear(key: string) {
    setError(null);
    try { onSaved(await api.saveSettings({ [key]: null })); } catch (err) { setError(err); }
  }

  return (
    <form className="card stack settings" onSubmit={save} aria-label={`${title} settings`}>
      <div className="row-plain">
        <h2 className="grow">{title}</h2>
        <span className={`badge ${live ? "st-sent" : ""}`}>{live ? "Ready" : "Not set up"}</span>
      </div>
      <p className="muted">{intro}</p>
      {fields.map((f) => {
        const fromEnv = f.source === "env";
        return (
          <label key={f.key}>
            {f.label}
            {fromEnv ? (
              <span className="fixed">{f.secret ? "set by the server" : f.value} <span className="muted">· from {f.env}; change it there</span></span>
            ) : f.secret ? (
              <span className="iconfield">
                <input type="password" autoComplete="new-password" value={draft[f.key] ?? ""} disabled={!canStore}
                  placeholder={f.set ? "•••••••• set — type to replace" : canStore ? "not set" : "needs the server key"}
                  onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })} aria-label={f.label} />
                {f.set && f.source === "app" && <button type="button" className="ghost danger" onClick={() => clear(f.key)}>Clear</button>}
              </span>
            ) : (
              <input value={draft[f.key] ?? f.value ?? ""} placeholder={f.default || ""}
                onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })} aria-label={f.label} />
            )}
            {f.help && <span className="muted">{f.help}</span>}
            {f.problem && <span className="error">{f.problem}</span>}
          </label>
        );
      })}
      <ErrorNote error={error} />
      {note && <p className="muted" role="status">{note}</p>}
      <div className="actions">
        <button type="submit" disabled={!dirty}>Save</button>
        {dirty && <button type="button" className="ghost" onClick={() => setDraft({})}>Discard</button>}
        <span className="muted">Then send yourself a test from <a href="/notifications">your notification settings</a>.</span>
      </div>
    </form>
  );
}
