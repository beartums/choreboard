import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type SwapRequestView } from "../api";
import { ErrorNote } from "../components/ErrorNote";

/** One swap, hand-off or undo request: where the notification's link lands,
 *  so it can be answered without hunting for it on the calendar. */
export function RequestPage() {
  const id = Number(useParams().id);
  const [r, setR] = useState<SwapRequestView | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { api.swapRequest(id).then(setR, setError); }, [id]);
  useEffect(load, [load]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try { await fn(); load(); } catch (e) { setError(e); } finally { setBusy(false); }
  }

  if (!r) return <section><h1>Request</h1>{error ? <ErrorNote error={error} /> : <p className="muted">Loading…</p>}</section>;
  const noun = r.kind === "give" ? "Hand-off" : r.kind === "undo" ? "Undo a swap" : "Swap";
  return (
    <section className="stack request-page">
      <h1>{noun} request</h1>
      <div className="card stack">
        {r.kind === "give" ? (
          <p><strong>{r.from_name}</strong> asks <strong>{r.to_name}</strong> to take {r.mine.what}.</p>
        ) : r.kind === "undo" ? (
          <p><strong>{r.from_name}</strong> asks <strong>{r.to_name}</strong> to undo their swap: {r.mine.what}
            {r.theirs && <> and {r.theirs.what}</>}.</p>
        ) : (
          <>
            <p><strong>{r.from_name}</strong> asks <strong>{r.to_name}</strong> to swap:</p>
            <ul>
              <li>{r.to_name} does {r.from_name}'s {r.mine.what}</li>
              {r.theirs && <li>{r.from_name} does {r.to_name}'s {r.theirs.what}</li>}
            </ul>
          </>
        )}
        {r.reason && <p className="note">Reason: {r.reason}</p>}
        {r.status === "pending" ? (
          <div className="actions">
            {r.can.includes("approve") && <button disabled={busy} onClick={() => act(() => api.answerSwap(r.id, true))}>
              {r.kind === "give" ? "Accept" : "Approve"}</button>}
            {r.can.includes("decline") && <button className="ghost" disabled={busy} onClick={() => act(() => api.answerSwap(r.id, false))}>Decline</button>}
            {r.can.includes("cancel") && <button className="ghost" disabled={busy} onClick={() => act(() => api.cancelSwap(r.id))}>Cancel request</button>}
            {!r.can.length && <span className="muted">Waiting for {r.to_name}.</span>}
          </div>
        ) : (
          <p><strong>{r.status[0].toUpperCase() + r.status.slice(1)}.</strong>{r.answer && ` ${r.answer}`}</p>
        )}
        <ErrorNote error={error} />
      </div>
      <p><Link to={`/calendar?date=${r.mine.date}`}>See it on the calendar</Link></p>
    </section>
  );
}
