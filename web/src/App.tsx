import { useCallback, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, type Me } from "./api";
import { ErrorNote } from "./components/ErrorNote";
import { CalendarPage } from "./pages/CalendarPage";
import { ChoreDetail } from "./pages/ChoreDetail";
import { ChoresPage } from "./pages/ChoresPage";
import { useUnread } from "./components/useUnread";
import { InboxPage } from "./pages/InboxPage";
import { NotificationsPage } from "./pages/NotificationsPage";
import { PeoplePage } from "./pages/PeoplePage";
import { SettingsPage } from "./pages/SettingsPage";
import { SetupPage } from "./pages/SetupPage";

export function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    api.me().then(setMe, setError);
  }, []);
  useEffect(load, [load]);

  if (error) return <main className="page"><ErrorNote error={error} /></main>;
  if (me && !me.needs_setup) return <Shell me={me} />;
  if (!me) return <main className="page"><p className="muted">Loading…</p></main>;
  return <SetupPage email={me.email} onDone={load} />;
}

function Shell({ me }: { me: Me }) {
  const { unread } = useUnread();
  return (
    <div className="shell">
      <header className="top">
        <span className="brand">Choreboard</span>
        <nav aria-label="Main">
          <NavLink to="/calendar">Calendar</NavLink>
          <NavLink to="/chores">Chores</NavLink>
          <NavLink to="/people">People</NavLink>
          {me.person?.role === "admin" && <NavLink to="/settings">Settings</NavLink>}
          <NavLink to="/inbox" aria-label={unread ? `Inbox, ${unread} unread` : "Inbox"}>
            Inbox{unread > 0 && <span className="count" aria-hidden>{unread > 99 ? "99+" : unread}</span>}
          </NavLink>
        </nav>
        <NavLink to="/notifications" className="who" title={`${me.email} · notification settings`}>{me.person?.name}</NavLink>
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
          <Route path="/notifications" element={<NotificationsPage />} />
          <Route path="*" element={<p>Nothing here. <NavLink to="/chores">Chores</NavLink></p>} />
        </Routes>
      </main>
    </div>
  );
}
