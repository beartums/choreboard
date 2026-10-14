import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, type Catalog, type Channel, type ChannelSetting, type Chore, type Delivery, type Subscription } from "../api";
import { ErrorNote } from "../components/ErrorNote";

const CHANNEL_NAME: Record<Channel, string> = { email: "Email", discord: "Discord" };

/** What you're told about, and where. Notifications are never about your
 *  own actions. */
export function NotificationsPage() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [channels, setChannels] = useState<Record<Channel, ChannelSetting> | null>(null);
  const [subs, setSubs] = useState<Subscription[]>([]);
  const [chores, setChores] = useState<Chore[]>([]);
  const [log, setLog] = useState<Delivery[]>([]);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      const [c, mine, ch, d] = await Promise.all([api.catalog(), api.myNotifications(), api.chores(), api.deliveries()]);
      setCatalog(c); setChannels(mine.channels); setSubs(mine.subscriptions); setChores(ch); setLog(d);
      setError(null);
    } catch (err) {
      setError(err);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  if (!catalog || !channels) return <section><h1>Notifications</h1><ErrorNote error={error} /></section>;
  const configured = (c: Channel) => catalog.channels.find((x) => x.channel === c)?.configured ?? false;
  const all = (event: string, channel: Channel) => subs.find((s) => s.event === event && s.channel === channel && s.chore_id == null);

  async function toggle(event: string, channel: Channel) {
    try {
      const s = all(event, channel);
      if (s) await api.unsubscribe(s.id);
      else await api.subscribe(event, channel);
      await load();
    } catch (err) {
      setError(err);
    }
  }

  return (
    <section>
      <h1>Notifications</h1>
      <p className="muted">You're told when someone <em>else</em> does something that affects you — never about your own actions.</p>
      <ErrorNote error={error} />

      <h2>Where</h2>
      <div className="grid2">
        {(["email", "discord"] as Channel[]).map((c) => (
          <ChannelCard key={c} channel={c} setting={channels[c]} configured={configured(c)} onSaved={load} />
        ))}
      </div>

      <h2>What, for all my chores</h2>
      <table className="subs" aria-label="Notifications for all my chores">
        <thead><tr><th>When</th>{(["email", "discord"] as Channel[]).map((c) => <th key={c}>{CHANNEL_NAME[c]}</th>)}</tr></thead>
        <tbody>
          {catalog.events.map((e) => (
            <tr key={e.kind}>
              <td>{e.label}</td>
              {(["email", "discord"] as Channel[]).map((c) => (
                <td key={c}>
                  <input type="checkbox" aria-label={`${e.label} by ${CHANNEL_NAME[c]}`} checked={!!all(e.kind, c)}
                    onChange={() => toggle(e.kind, c)} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Only for particular chores</h2>
      <p className="muted">Hear about one chore without hearing about all of them.</p>
      <PerChore catalog={catalog} chores={chores} subs={subs.filter((s) => s.chore_id != null)} onChanged={load} onError={setError} />

      <h2>Recent</h2>
      {log.length === 0 ? <p className="muted">Nothing sent yet.</p> : (
        <ul className="list" aria-label="Recent notifications">
          {log.map((d) => (
            <li key={d.id} className="row">
              <span className="grow">
                <strong>{d.title}</strong>
                <span className="muted block">{CHANNEL_NAME[d.channel]} · {new Date(d.created_at).toLocaleString()}
                  {d.last_error && ` · ${d.last_error}`}</span>
              </span>
              <span className={`badge st-${d.status}`}>{d.status === "queued" && d.attempts ? "retrying" : d.status}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ChannelCard({ channel, setting, configured, onSaved }:
  { channel: Channel; setting: ChannelSetting; configured: boolean; onSaved: () => void }) {
  const [address, setAddress] = useState(setting.default ? "" : setting.address ?? "");
  const [error, setError] = useState<unknown>(null);
  const [note, setNote] = useState("");

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(null); setNote("");
    try { await api.setChannel(channel, address.trim() || null); setNote("Saved."); onSaved(); } catch (err) { setError(err); }
  }
  async function test() {
    setError(null);
    try { await api.testChannel(channel); setNote("Test queued — it shows under Recent in a few seconds."); setTimeout(onSaved, 4000); }
    catch (err) { setError(err); }
  }

  return (
    <form className="card stack" onSubmit={save} aria-label={`${CHANNEL_NAME[channel]} settings`}>
      <h3>{CHANNEL_NAME[channel]}</h3>
      {!configured && <p className="note">This server can't send {CHANNEL_NAME[channel].toLowerCase()} yet; ask whoever runs it.</p>}
      {channel === "email" ? (
        <label>Send to
          <input type="email" value={address} onChange={(e) => setAddress(e.target.value)}
            placeholder={setting.default && setting.address ? `${setting.address} (your sign-in)` : "you@example.com"} />
        </label>
      ) : (
        <label>Your Discord user id
          <input inputMode="numeric" value={address} onChange={(e) => setAddress(e.target.value)} placeholder="e.g. 123456789012345678" />
          <span className="muted">Discord → Settings → Advanced → Developer Mode on; then right-click your name → Copy User ID.
            You need to be in a server with the Choreboard bot.</span>
        </label>
      )}
      <ErrorNote error={error} />
      {note && <p className="muted" role="status">{note}</p>}
      <div className="actions">
        <button type="submit">Save</button>
        <button type="button" className="ghost" onClick={test} disabled={!configured || !(address || setting.address)}>Send a test</button>
      </div>
    </form>
  );
}

function PerChore({ catalog, chores, subs, onChanged, onError }: {
  catalog: Catalog; chores: Chore[]; subs: Subscription[]; onChanged: () => void; onError: (e: unknown) => void;
}) {
  const mine = catalog.events.filter((e) => e.scope === "mine");
  const [chore, setChore] = useState<number | "">(chores[0]?.id ?? "");
  const [event, setEvent] = useState(mine[0]?.kind ?? "");
  const [channel, setChannel] = useState<Channel>("email");
  const choreName = (id: number | null) => { const c = chores.find((x) => x.id === id); return c ? `${c.icon} ${c.title}`.trim() : `#${id}`; };
  const label = (k: string) => catalog.events.find((e) => e.kind === k)?.label ?? k;

  async function add(e: FormEvent) {
    e.preventDefault();
    if (chore === "") return;
    try { await api.subscribe(event, channel, chore); onChanged(); } catch (err) { onError(err); }
  }

  return (
    <div className="stack">
      {subs.length > 0 && (
        <ul className="list" aria-label="Per-chore notifications">
          {subs.map((s) => (
            <li key={s.id} className="row">
              <span className="grow"><strong>{choreName(s.chore_id)}</strong> <span className="muted">{label(s.event)} · {CHANNEL_NAME[s.channel]}</span></span>
              <button className="ghost danger" onClick={async () => { try { await api.unsubscribe(s.id); onChanged(); } catch (err) { onError(err); } }}>Remove</button>
            </li>
          ))}
        </ul>
      )}
      <form className="card grid3" onSubmit={add} aria-label="Add a per-chore notification">
        <label>Chore<select value={chore} onChange={(e) => setChore(Number(e.target.value))}>
          {chores.map((c) => <option key={c.id} value={c.id}>{`${c.icon} ${c.title}`.trim()}</option>)}</select></label>
        <label>When<select value={event} onChange={(e) => setEvent(e.target.value)}>
          {mine.map((e) => <option key={e.kind} value={e.kind}>{e.label}</option>)}</select></label>
        <label>How<select value={channel} onChange={(e) => setChannel(e.target.value as Channel)}>
          <option value="email">Email</option><option value="discord">Discord</option></select></label>
        <div className="actions"><button type="submit" disabled={chore === ""}>Add</button></div>
      </form>
    </div>
  );
}
