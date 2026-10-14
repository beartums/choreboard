import { useCallback, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, type Me } from "./api";
import { ErrorNote } from "./components/ErrorNote";
import { CalendarPage } from "./pages/CalendarPage";
import { ChoreDetail } from "./pages/ChoreDetail";
import { ChoresPage } from "./pages/ChoresPage";
import { PeoplePage } from "./pages/PeoplePage";
import { SetupPage } from "./pages/SetupPage";

export function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    api.me().then(setMe, setError);
  }, []);
  useEffect(load, [load]);

  if (error) return <main className="page"><ErrorNote error={error} /></main>;
  if (!me) return <main className="page"><p className="muted">Loading…</p></main>;
  if (me.needs_setup) return <SetupPage email={me.email} onDone={load} />;

  return (
    <div className="shell">
      <header className="top">
        <span className="brand">Choreboard</span>
        <nav aria-label="Main">
          <NavLink to="/calendar">Calendar</NavLink>
          <NavLink to="/chores">Chores</NavLink>
          <NavLink to="/people">People</NavLink>
        </nav>
        <span className="who" title={me.email}>{me.person?.name}</span>
      </header>
      <main className="page">
        <Routes>
          <Route path="/" element={<Navigate to="/calendar" replace />} />
          <Route path="/calendar" element={me.person ? <CalendarPage me={me.person} /> : null} />
          <Route path="/chores" element={<ChoresPage />} />
          <Route path="/chores/:id" element={<ChoreDetail />} />
          <Route path="/people" element={<PeoplePage />} />
          <Route path="*" element={<p>Nothing here. <NavLink to="/chores">Chores</NavLink></p>} />
        </Routes>
      </main>
    </div>
  );
}
