import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError, timeWindow, type Chore, type Entry, type PendingRequest, type Person, type Settle,
  type Status } from "../api";
import { addDays, dayLabel, today } from "../dates";
import { ErrorNote } from "./ErrorNote";
import { SwapPicker, type Candidate } from "./SwapPicker";

type Mode = null | "covered" | "failed" | "failed_covered" | "give" | "propose_give" | "skip" | "swap" | "propose_swap"
  | "undo" | "request_undo";

/** Everything you can do to one occurrence: record what happened, or plan a
 *  change for that date only. The household's rules (DESIGN.md) are enforced
 *  by the API; this asks for what each needs -- a reason, who covered, how
 *  many points to take. */
export function OccurrencePanel({ entry, chores, people, onClose, onChanged }: {
  entry: Entry; me: Person; chores: Chore[]; people: Person[]; onClose: () => void; onChanged: () => void;
}) {
  const [mode, setMode] = useState<Mode>(null);
  const [error, setError] = useState<unknown>(null);
  // An admin's change hit a pending swap: ask how to settle it, then retry.
  const [settle, setSettle] = useState<{ req: PendingRequest; retry: (how: Settle) => Promise<unknown> } | null>(null);
  const chore = chores.find((c) => c.id === entry.chore_id);
  const name = (id: number | null) => people.find((p) => p.id === id)?.name ?? "?";
  const others = people.filter((p) => p.id !== entry.person_id);

  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  async function run(fn: (pending?: Settle) => Promise<unknown>) {
    setError(null);
    setSettle(null);
    try { await fn(); onChanged(); } catch (err) {
      if (err instanceof ApiError && err.data.code === "pending_swap" && err.data.request) {
        setSettle({ req: err.data.request as PendingRequest, retry: fn });
      } else setError(err);
    }
  }

  const recordDone = () => run(() => api.record(entry.id, { status: "done" }));
  const recordNotNeeded = () => run(() => api.record(entry.id, { status: "skipped" }));
  const undoChange = () => run(() => api.removeOverride(entry.group_id ?? entry.override_id!));

  const title = `${chore?.icon ? chore.icon + " " : ""}${chore?.title ?? "Chore"}`;
  // What this person may do, from the server. An old server sends no `can`:
  // then offer everything and let the server say no.
  const can = (a: string) => entry.can === undefined || entry.can.includes(a);
  const why = (a: string) => entry.cannot?.[a];
  const ps = entry.pending_swap;

  return (
    <div className="backdrop" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={title} className="panel card stack" onClick={(e) => e.stopPropagation()}>
        <div className="row-plain">
          <h2 className="grow">{title}</h2>
          <button className="ghost" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <p className="muted">
          {dayLabel(entry.date)}{timeWindow(entry.available, entry.due) && ` · ${timeWindow(entry.available, entry.due)}`} · <strong>{name(entry.person_id)}</strong>
          {entry.scheduled_person_id != null && ` (instead of ${name(entry.scheduled_person_id)})`}
          {chore && ` · ${chore.points} pt${chore.points === 1 ? "" : "s"}`}
        </p>
        {entry.unscheduled && <p className="note">This day is no longer on the schedule (its rotation changed or now
          starts later), but what was recorded is kept.</p>}
        {entry.reason && <p className="note">{entry.group_id ? "Swap" : "Change"}: {entry.reason}</p>}

        {ps && (
          <div className="note stack">
            <span>⇄ <strong>{ps.kind === "undo" ? "Undo pending" : ps.kind === "give" ? "Hand-off pending" : "Swap pending"}</strong>: {ps.kind === "undo"
              ? `${name(ps.from_person)} asked to undo this swap`
              : ps.kind === "give" ? `${name(ps.from_person)} asked ${name(ps.to_person)} to take this`
              : ps.side === "from" ? `${name(ps.from_person)} offered this to ${name(ps.to_person)}`
              : `${name(ps.from_person)} asked ${name(ps.to_person)} to take this`}
              {ps.reason && ` — ${ps.reason}`}. Nothing changes until {name(ps.to_person)} approves.</span>
            <div className="actions">
              {can("approve_swap") && <button onClick={() => run(() => api.answerSwap(ps.id, true))}>{ps.kind === "give" ? "Accept" : "Approve swap"}</button>}
              {can("decline_swap") && <button className="ghost" onClick={() => run(() => api.answerSwap(ps.id, false))}>Decline</button>}
              {can("cancel_swap") && <button className="ghost" onClick={() => run(() => api.cancelSwap(ps.id))}>Cancel request</button>}
            </div>
          </div>
        )}

        {entry.outcome && (
          <p><strong>{describeOutcome(entry, name)}</strong>{entry.outcome.note && ` — ${entry.outcome.note}`}</p>
        )}
        {entry.skipped && <p>Skipped for this date.</p>}

        {mode === null ? (
          <ActionBar can={(a) => can(a) && (entry.outcome ? a === "undo" : entry.skipped ? a === "unskip" : a !== "undo" && a !== "unskip")}
            why={why} pending={!!ps} onPick={(a) => {
            if (a === "done") recordDone();
            else if (a === "skipped") recordNotNeeded();
            else if (a === "unskip" || a === "undo_swap" || a === "undo_change") undoChange();
            else setMode(a as Mode);
          }} />
        ) : mode === "undo" ? (
          <ReasonForm label="Why undo it?" submit="Undo" onSubmit={(note) => run(() => api.undo(entry.outcome!.id, note))} onCancel={() => setMode(null)} />
        ) : mode === "covered" || mode === "failed" || mode === "failed_covered" ? (
          <OutcomeForm status={mode} points={chore?.points ?? 0} others={others} assignee={name(entry.person_id)}
            onSubmit={(b) => run(() => api.record(entry.id, b))} onCancel={() => setMode(null)} />
        ) : mode === "give" ? (
          <GiveForm others={others} onSubmit={(pid, reason) => run((p) => api.reassign(entry.id, pid, reason, p))} onCancel={() => setMode(null)} />
        ) : mode === "propose_give" ? (
          <GiveForm others={others} proposal onSubmit={(pid, reason) => run(() => api.requestGive(entry.id, pid, reason))}
            onCancel={() => setMode(null)} />
        ) : mode === "request_undo" ? (
          <ReasonForm label="Why undo the swap?" submit="Ask them"
            onSubmit={(reason) => run(() => api.requestUndo(entry.id, reason))} onCancel={() => setMode(null)} />
        ) : mode === "skip" ? (
          <ReasonForm label="Why skip it?" submit="Skip this date" optional
            onSubmit={(reason) => run((p) => api.skip(entry.id, reason, p))} onCancel={() => setMode(null)} />
        ) : mode === "swap" || mode === "propose_swap" ? (
          <SwapForm entry={entry} chores={chores} people={people} proposal={!can("swap")}
            onSubmit={(other, reason) => run((p) => can("swap") ? api.swap(entry.id, other, reason, p) : api.proposeSwap(entry.id, other, reason))}
            onCancel={() => setMode(null)} />
        ) : null}
        {settle && (
          <div className="note stack" role="alert">
            <span>⇄ This is in a pending {settle.req.kind === "undo" ? "request to undo a swap" : "swap"}{" "}
              ({settle.req.from_name} asked {settle.req.to_name}{settle.req.reason && ` — ${settle.req.reason}`}).
              Doing this settles it first: approving {settle.req.kind === "undo" ? "undoes the swap" : "makes the swap"},
              declining drops the request.</span>
            <div className="actions">
              <button onClick={() => run(() => settle.retry("approve"))}>Approve it, then do this</button>
              <button onClick={() => run(() => settle.retry("decline"))}>Decline it, then do this</button>
              <button className="ghost" onClick={() => setSettle(null)}>Never mind</button>
            </div>
          </div>
        )}
        <ErrorNote error={error} />
      </div>
    </div>
  );
}

/* The actions, as icons -- the same set, order and ⋯ menu as nav's chore
 * widget. [action, icon, label]. The server's `can` decides which show. */
const ICONS: [string, string, string][] = [
  ["done", "✓", "Mark done"],
  ["covered", "✓⁺", "Someone else did it"],
  ["propose_swap", "⇄", "Ask to swap"],
  ["swap", "⇄", "Swap"],
  ["give", "⇄⁺", "Give to someone"],
  ["propose_give", "⇄⁺", "Ask someone to take it"],
  ["skip", "⏭", "Skip this day"],
  ["failed", "✗", "Missed"],
  ["undo", "↶", "Undo"],
  ["undo_swap", "⇄↶", "Undo the swap"],
  ["request_undo", "⇄↶", "Ask to undo the swap"],
  ["unskip", "↺", "Don't skip it"],
  ["undo_change", "↶", "Undo the change"],
];
const MORE: [string, string, string][] = [
  ["failed_covered", "✗⁺", "Missed, someone covered"],
  ["skipped", "∅", "Not needed"],
];
const WHY: Record<string, string> = { done: "Done", propose_swap: "Swap", undo: "Undo", undo_swap: "Undo swap",
  request_undo: "Undo swap" };

function ActionBar({ can, why, pending, onPick }: {
  can: (a: string) => boolean; why: (a: string) => string | undefined; pending: boolean; onPick: (a: string) => void;
}) {
  const [menu, setMenu] = useState(false);
  const shown = ICONS.filter(([a]) => can(a) && !(pending && (a === "propose_swap" || a === "propose_give")));
  const more = MORE.filter(([a]) => can(a));
  const whys = Object.keys(WHY).filter((a) => !can(a) && why(a));
  return (
    <div className="stack">
      <div className="icon-bar" role="toolbar" aria-label="Actions">
        {shown.map(([a, icon, label]) => (
          <button key={a} className={`icon-btn${a === "done" ? " primary" : ""}`} title={label} aria-label={label}
            onClick={() => onPick(a)}>{icon}</button>
        ))}
        {more.length > 0 && (
          <span className="more">
            <button className="icon-btn" title="More" aria-label="More" aria-haspopup="menu" aria-expanded={menu}
              onClick={() => setMenu(!menu)}>⋯</button>
            {menu && (
              <div className="menu" role="menu">
                {more.map(([a, icon, label]) => (
                  <button key={a} role="menuitem" onClick={() => { setMenu(false); onPick(a); }}>{icon} {label}</button>
                ))}
              </div>
            )}
          </span>
        )}
        {!shown.length && !more.length && !pending && <span className="muted">Nothing you can do with this one.</span>}
      </div>
      {whys.map((a) => <p key={a} className="muted why">{WHY[a]}: {why(a)}.</p>)}
    </div>
  );
}

function describeOutcome(e: Entry, name: (id: number | null) => string): string {
  const o = e.outcome!;
  switch (o.status) {
    case "done": return `Done by ${name(o.done_by_id)}`;
    case "skipped": return "Didn't need doing";
    case "failed": return `Missed by ${name(o.assignee_id)}`;
    case "covered": return `Covered by ${name(o.done_by_id)}`;
    case "failed_covered": return `Missed by ${name(o.assignee_id)}, covered by ${name(o.done_by_id)}`;
  }
}

function ReasonForm({ label, submit, optional, onSubmit, onCancel }:
  { label: string; submit: string; optional?: boolean; onSubmit: (r: string) => void; onCancel: () => void }) {
  const [reason, setReason] = useState("");
  return (
    <form className="stack" onSubmit={(e: FormEvent) => { e.preventDefault(); onSubmit(reason.trim()); }}>
      <label>{label}<input value={reason} onChange={(e) => setReason(e.target.value)} required={!optional} autoFocus /></label>
      <div className="actions">
        <button type="submit" disabled={!optional && !reason.trim()}>{submit}</button>
        <button type="button" className="ghost" onClick={onCancel}>Back</button>
      </div>
    </form>
  );
}

function OutcomeForm({ status, points, others, assignee, onSubmit, onCancel }: {
  status: "covered" | "failed" | "failed_covered"; points: number; others: Person[]; assignee: string;
  onSubmit: (b: { status: Status; note: string; done_by?: number; deduct?: number }) => void; onCancel: () => void;
}) {
  const covers = status !== "failed";
  const deducts = status !== "covered";
  const [by, setBy] = useState<number | "">(others[0]?.id ?? "");
  const [deduct, setDeduct] = useState(points);
  const [note, setNote] = useState("");
  const ok = note.trim() && (!covers || by !== "");

  return (
    <form className="stack" onSubmit={(e: FormEvent) => {
      e.preventDefault();
      onSubmit({ status, note: note.trim(), ...(covers ? { done_by: Number(by) } : {}), ...(deducts ? { deduct } : {}) });
    }}>
      {covers && (
        <label>Who did it
          <select value={by} onChange={(e) => setBy(Number(e.target.value))}>
            {others.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
      )}
      {deducts && (
        <label>Points to take from {assignee}
          <input type="number" min={0} value={deduct} onChange={(e) => setDeduct(Math.max(0, Number(e.target.value)))} />
        </label>
      )}
      <label>Why<input value={note} onChange={(e) => setNote(e.target.value)} required autoFocus /></label>
      <p className="muted">{covers ? "Whoever did it gets the chore's points." : ""} {deducts ? "Nothing is taken unless you say so; 0 is fine." : ""}</p>
      <div className="actions">
        <button type="submit" disabled={!ok}>Record</button>
        <button type="button" className="ghost" onClick={onCancel}>Back</button>
      </div>
    </form>
  );
}

function GiveForm({ others, proposal, onSubmit, onCancel }: {
  others: Person[]; proposal?: boolean; onSubmit: (pid: number, r: string) => void; onCancel: () => void;
}) {
  const [pid, setPid] = useState<number | "">(others[0]?.id ?? "");
  const [reason, setReason] = useState("");
  return (
    <form className="stack" onSubmit={(e: FormEvent) => { e.preventDefault(); if (pid !== "") onSubmit(pid, reason.trim()); }}>
      <label>{proposal ? "Ask" : "Give it to"}
        <select value={pid} onChange={(e) => setPid(Number(e.target.value))}>
          {others.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </label>
      <label>Why<input value={reason} onChange={(e) => setReason(e.target.value)} placeholder={proposal ? "" : "optional"}
        required={proposal} /></label>
      {proposal && <p className="muted">They'll be asked to accept; it stays yours until they do.</p>}
      <div className="actions">
        <button type="submit" disabled={pid === "" || (proposal && !reason.trim())}>{proposal ? "Ask them" : "Give it"}</button>
        <button type="button" className="ghost" onClick={onCancel}>Back</button>
      </div>
    </form>
  );
}

/** Pick the other side of a swap -- any scheduled, unrecorded occurrence of
 *  someone else's, on any date ahead -- with the three-step picker. */
function SwapForm({ entry, chores, people, proposal, onSubmit, onCancel }: {
  entry: Entry; chores: Chore[]; people: Person[]; proposal?: boolean;
  onSubmit: (other: string, reason: string) => void; onCancel: () => void;
}) {
  const [weeks, setWeeks] = useState(12);
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [pick, setPick] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<unknown>(null);
  const from = today() < entry.date ? today() : entry.date;

  useEffect(() => {
    api.calendar(from, addDays(from, weeks * 7)).then((all) => setCandidates(all.filter((e) =>
      e.id !== entry.id && e.person_id !== entry.person_id && e.rule_id != null && !e.skipped && !e.outcome
      && !e.pending_swap && e.date >= today()).map((e) => {
      const c = chores.find((x) => x.id === e.chore_id);
      return { key: e.id, date: e.date, person: people.find((p) => p.id === e.person_id)?.name ?? "?",
               chore: `${c?.icon ? c.icon + " " : ""}${c?.title ?? "Chore"}`, window: timeWindow(e.available, e.due) };
    })), setError);
  }, [from, weeks, entry.id, entry.person_id, chores, people]);

  return (
    <form className="stack" onSubmit={(e: FormEvent) => { e.preventDefault(); onSubmit(pick, reason.trim()); }}>
      <h3>{proposal ? "Ask to swap" : "Swap"}</h3>
      {candidates ? <SwapPicker candidates={candidates} value={pick} onChange={setPick} /> : <p className="muted">Loading…</p>}
      <button type="button" className="ghost" onClick={() => setWeeks(weeks + 12)}>Look further ahead ({weeks + 12} weeks)</button>
      <label>Why<input value={reason} onChange={(e) => setReason(e.target.value)} required /></label>
      <ErrorNote error={error} />
      {proposal && <p className="muted">They'll be asked to approve it; nothing changes until they do.</p>}
      <div className="actions">
        <button type="submit" disabled={!pick || !reason.trim()}>{proposal ? "Ask them" : "Swap"}</button>
        <button type="button" className="ghost" onClick={onCancel}>Back</button>
      </div>
    </form>
  );
}
