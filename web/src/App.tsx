import { useCallback, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, ApiError, getActingAs, setActingAs, type Me, type Person } from "./api";
import { ErrorNote } from "./components/ErrorNote";
import { CalendarPage } from "./pages/CalendarPage";
import { ChoreDetail } from "./pages/ChoreDetail";
import { ChoresPage } from "./pages/ChoresPage";
import { useUnread } from "./components/useUnread";
import { MeContext } from "./me";
import { InboxPage } from "./pages/InboxPage";
import { NotificationsPage } from "./pages/NotificationsPage";
import { PeoplePage } from "./pages/PeoplePage";
import { SettingsPage } from "./pages/SettingsPage";
import { TokensPage } from "./pages/TokensPage";
import { SetupPage } from "./pages/SetupPage";

export function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    api.me().then(setMe, (err) => {
      // Acting as someone who's gone (or you're no longer an admin): be yourself.
      if (getActingAs() && err instanceof ApiError && err.status === 403) {
        setActingAs(null);
        api.me().then(setMe, setError);
      } else setError(err);
    });
  }, []);
  const actAs = useCallback((id: number | null) => { setActingAs(id); setMe(null); load(); }, [load]);
  useEffect(load, [load]);

  if (error) return <main className="page"><ErrorNote error={error} /></main>;
  // Keyed by who you're acting as, so every page reloads as them.
  if (me && !me.needs_setup) return <Shell key={me.person?.id ?? 0} me={me} actAs={actAs} />;
  if (!me) return <main className="page"><p className="muted">Loading…</p></main>;
  return <SetupPage email={me.email} onDone={load} />;
}

function Shell({ me, actAs }: { me: Me; actAs: (id: number | null) => void }) {
  const { unread } = useUnread();
  const p = me.person;
  return (
    <MeContext.Provider value={me.person}>
    <div className="shell">
      <header className="top">
        <span className="brand">Choreboard</span>
        <nav aria-label="Main">
          <NavLink to="/calendar">Calendar</NavLink>
          <NavLink to="/chores">Chores</NavLink>
          {me.person?.role === "admin" && <NavLink to="/people">People</NavLink>}
          {me.person?.role === "admin" && <NavLink to="/settings">Settings</NavLink>}
          <NavLink to="/inbox" aria-label={unread ? `Inbox, ${unread} unread` : "Inbox"}>
            Inbox{unread > 0 && <span className="count" aria-hidden>{unread > 99 ? "99+" : unread}</span>}
          </NavLink>
        </nav>
        {p?.via_name ? (
          <button className="acting" onClick={() => actAs(null)} title={`You are ${p.via_name}, acting as ${p.name}. Click to stop.`}>
            👤 as {p.name} ✕
          </button>
        ) : p?.role === "admin" && <ActAsPicker me={p} onPick={actAs} />}
        <NavLink to="/notifications" className="who" title={`${me.email} · notification settings`}>{p?.via_name ?? p?.name}</NavLink>
      </header>
      <main className="page">
        <Routes>
          <Route path="/" element={<Navigate to="/calendar" replace />} />
          <Route path="/calendar" element={me.person ? <CalendarPage me={me.person} /> : null} />
          <Route path="/chores" element={<ChoresPage />} />
          <Route path="/chores/:id" element={<ChoreDetail />} />
          <Route path="/people" element={<PeoplePage />} />
          <Route path="/inbox" element={<InboxPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/settings/tokens" element={me.person ? <TokensPage me={me.person} /> : null} />
          <Route path="/notifications" element={<NotificationsPage />} />
          <Route path="*" element={<p>Nothing here. <NavLink to="/chores">Chores</NavLink></p>} />
        </Routes>
      </main>
    </div>
    </MeContext.Provider>
  );
}

/** Admins: act as someone -- their view and permissions, recorded as you. */
function ActAsPicker({ me, onPick }: { me: Person; onPick: (id: number) => void }) {
  const [people, setPeople] = useState<Person[] | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => { if (open && !people) api.people().then(setPeople, () => setPeople([])); }, [open, people]);
  return (
    <div className="act-as">
      <button className="ghost" aria-label="Act as someone else" aria-expanded={open} title="Act as someone else"
        onClick={() => setOpen(!open)}>👤</button>
      {open && (
        <div className="act-as-menu" role="menu" aria-label="Act as">
          <span className="muted">Act as:</span>
          {(people ?? []).filter((x) => x.id !== me.id).map((x) => (
            <button key={x.id} role="menuitem" className="chip" onClick={() => { setOpen(false); onPick(x.id); }}>{x.name}</button>
          ))}
        </div>
      )}
    </div>
  );
}
