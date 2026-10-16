import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type InboxItem } from "../api";
import { ErrorNote } from "../components/ErrorNote";
import { inboxChanged } from "../components/useUnread";

function when(iso: string): string {
  const d = new Date(iso);
  const mins = Math.round((Date.now() - d.getTime()) / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 24 * 60) return `${Math.round(mins / 60)} h ago`;
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

/** Your notifications: everything you haven't ignored, newest first. */
export function InboxPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const [unread, setUnread] = useState(0);
  const [onlyUnread, setOnlyUnread] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    api.inbox(onlyUnread).then((r) => { setItems(r.items); setUnread(r.unread); setError(null); }, setError);
  }, [onlyUnread]);
  useEffect(load, [load]);

  async function mark(ids: number[] | undefined, read: boolean) {
    try {
      await api.markRead(ids, read);
      inboxChanged();
      load();
    } catch (err) {
      setError(err);
    }
  }

  async function open(n: InboxItem) {
    if (!n.read_at) await mark([n.id], true);
    if (n.path) navigate(n.path);
    else if (n.date) navigate(`/calendar?date=${n.date}`);
  }

  return (
    <section>
      <div className="calhead">
        <h1>Inbox {unread > 0 && <span className="muted period">{unread} unread</span>}</h1>
        <div className="actions">
          <label className="inline"><input type="checkbox" checked={onlyUnread} onChange={(e) => setOnlyUnread(e.target.checked)} /> Unread only</label>
          <button className="ghost" onClick={() => mark(undefined, true)} disabled={unread === 0}>Mark all read</button>
        </div>
      </div>
      <ErrorNote error={error} />
      {items && items.length === 0 && (
        <p className="muted">
          {onlyUnread ? "Nothing unread." : "Nothing yet."} Choose what you hear about in <Link to="/notifications">notification settings</Link>.
        </p>
      )}
      {items && items.length > 0 && (
        <ul className="list" aria-label="Notifications">
          {items.map((n) => (
            <li key={n.id} className={`row inbox${n.read_at ? "" : " unread"}`}>
              <button className="grow plain" onClick={() => open(n)} aria-label={`${n.read_at ? "" : "Unread: "}${n.title}`}>
                <strong>{n.title}</strong>
                <span className="block">{n.body}</span>
                <span className="muted block">{when(n.created_at)}{n.date && " · open the calendar"}</span>
              </button>
              <button className="ghost" onClick={() => mark([n.id], !n.read_at)}
                aria-label={n.read_at ? `Mark "${n.title}" unread` : `Mark "${n.title}" read`}>
                {n.read_at ? "Mark unread" : "Mark read"}
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="muted"><Link to="/notifications">Notification settings</Link></p>
    </section>
  );
}
