import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api, timeWindow, type Chore, type Entry, type Person, type Status } from "../api";
import { addDays, dayLabel, today } from "../dates";
import { ErrorNote } from "./ErrorNote";

type Mode = null | "covered" | "failed" | "failed_covered" | "give" | "skip" | "swap" | "undo";

/** Everything you can do to one occurrence: record what happened, or plan a
 *  change for that date only. The household's rules (DESIGN.md) are enforced
 *  by the API; this asks for what each needs -- a reason, who covered, how
 *  many points to take. */
export function OccurrencePanel({ entry, me, chores, people, onClose, onChanged }: {
  entry: Entry; me: Person; chores: Chore[]; people: Person[]; onClose: () => void; onChanged: () => void;
}) {
  const [mode, setMode] = useState<Mode>(null);
  const [error, setError] = useState<unknown>(null);
  const chore = chores.find((c) => c.id === entry.chore_id);
  const name = (id: number | null) => people.find((p) => p.id === id)?.name ?? "?";
  const others = people.filter((p) => p.id !== entry.person_id);

  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  async function run(fn: () => Promise<unknown>) {
    setError(null);
    try { await fn(); onChanged(); } catch (err) { setError(err); }
  }

  const recordDone = () => run(() => api.record(entry.id, { status: "done" }));
  const recordNotNeeded = () => run(() => api.record(entry.id, { status: "skipped" }));
  const undoChange = () => run(() => api.removeOverride(entry.group_id ?? entry.override_id!));

  const title = `${chore?.icon ? chore.icon + " " : ""}${chore?.title ?? "Chore"}`;
  const isOverride = entry.override_id != null;
  // What this person may do, from the server. An old server sends no `can`:
  // then offer everything and let the server say no.
  const can = (a: string) => entry.can === undefined || entry.can.includes(a);
  const why = (a: string) => entry.cannot?.[a];
  const ps = entry.pending_swap;
  const admin = can("failed");

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
            <span>⇄ <strong>Swap pending</strong>: {ps.side === "from"
              ? `${name(ps.from_person)} offered this to ${name(ps.to_person)}` : `${name(ps.from_person)} asked ${name(ps.to_person)} to take this`}
              {ps.reason && ` — ${ps.reason}`}. Nothing changes until {name(ps.to_person)} approves.</span>
            <div className="actions">
              {can("approve_swap") && <button onClick={() => run(() => api.answerSwap(ps.id, true))}>Approve swap</button>}
              {can("decline_swap") && <button className="ghost" onClick={() => run(() => api.answerSwap(ps.id, false))}>Decline</button>}
              {can("cancel_swap") && <button className="ghost" onClick={() => run(() => api.cancelSwap(ps.id))}>Cancel request</button>}
            </div>
          </div>
        )}

        {entry.outcome ? (
          <>
            <p><strong>{describeOutcome(entry, name)}</strong>{entry.outcome.note && ` — ${entry.outcome.note}`}</p>
            {mode === "undo"
              ? <ReasonForm label="Why undo it?" submit="Undo" onSubmit={(note) => run(() => api.undo(entry.outcome!.id, note))} onCancel={() => setMode(null)} />
              : can("undo") ? <div className="actions"><button className="ghost" onClick={() => setMode("undo")}>Undo this</button></div>
              : why("undo") && <p className="muted">Undo: {why("undo")}.</p>}
          </>
        ) : entry.skipped ? (
          <>
            <p>Skipped for this date.</p>
            {can("unskip") && <div className="actions"><button className="ghost" onClick={undoChange}>Don't skip it</button></div>}
          </>
        ) : mode === null ? (
          <>
            <Section title="What happened">
              {can("done") ? <button onClick={recordDone}>{entry.person_id === me.id ? "I did it" : `${name(entry.person_id)} did it`}</button>
                : why("done") && <span className="muted">{why("done")}.</span>}
              {can("covered") && <button className="ghost" onClick={() => setMode("covered")}>Someone else did it</button>}
              {can("failed") && <button className="ghost" onClick={() => setMode("failed")}>Missed</button>}
              {can("failed_covered") && <button className="ghost" onClick={() => setMode("failed_covered")}>Missed, someone covered</button>}
              {can("skipped") && <button className="ghost" onClick={recordNotNeeded}>Didn't need doing</button>}
            </Section>
            <Section title={admin ? "Plan a change, this date only" : "Swap"}>
              {can("give") && entry.rule_id != null && <button className="ghost" onClick={() => setMode("give")}>Give to someone</button>}
              {(can("swap") || can("propose_swap")) && entry.rule_id != null && !ps &&
                <button className="ghost" onClick={() => setMode("swap")}>{can("swap") ? "Swap with another day" : "Ask to swap…"}</button>}
              {!can("swap") && !can("propose_swap") && why("propose_swap") && <span className="muted">{why("propose_swap")}.</span>}
              {can("skip") && entry.rule_id != null && <button className="ghost" onClick={() => setMode("skip")}>Skip this date</button>}
              {can("undo_change") && isOverride && <button className="ghost" onClick={undoChange}>
                {entry.group_id ? "Undo the swap" : entry.rule_id == null ? "Remove this extra one" : "Undo the change"}</button>}
            </Section>
          </>
        ) : mode === "covered" || mode === "failed" || mode === "failed_covered" ? (
          <OutcomeForm status={mode} points={chore?.points ?? 0} others={others} assignee={name(entry.person_id)}
            onSubmit={(b) => run(() => api.record(entry.id, b))} onCancel={() => setMode(null)} />
        ) : mode === "give" ? (
          <GiveForm others={others} onSubmit={(pid, reason) => run(() => api.reassign(entry.id, pid, reason))} onCancel={() => setMode(null)} />
        ) : mode === "skip" ? (
          <ReasonForm label="Why skip it?" submit="Skip this date" optional
            onSubmit={(reason) => run(() => api.skip(entry.id, reason))} onCancel={() => setMode(null)} />
        ) : mode === "swap" ? (
          <SwapForm entry={entry} chores={chores} people={people} proposal={!can("swap")}
            onSubmit={(other, reason) => run(() => can("swap") ? api.swap(entry.id, other, reason) : api.proposeSwap(entry.id, other, reason))}
            onCancel={() => setMode(null)} />
        ) : null}
        <ErrorNote error={error} />
      </div>
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

function Section({ title, children }: { title: string; children: ReactNode }) {
  return <div className="stack"><h3>{title}</h3><div className="actions">{children}</div></div>;
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

function GiveForm({ others, onSubmit, onCancel }: { others: Person[]; onSubmit: (pid: number, r: string) => void; onCancel: () => void }) {
  const [pid, setPid] = useState<number | "">(others[0]?.id ?? "");
  const [reason, setReason] = useState("");
  return (
    <form className="stack" onSubmit={(e: FormEvent) => { e.preventDefault(); if (pid !== "") onSubmit(pid, reason.trim()); }}>
      <label>Give it to
        <select value={pid} onChange={(e) => setPid(Number(e.target.value))}>
          {others.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </label>
      <label>Why<input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="optional" /></label>
      <div className="actions">
        <button type="submit" disabled={pid === ""}>Give it</button>
        <button type="button" className="ghost" onClick={onCancel}>Back</button>
      </div>
    </form>
  );
}

/** Pick the other side of a swap: any scheduled, unrecorded occurrence of
 *  someone else's, on any date -- tomorrow's or months away. */
function SwapForm({ entry, chores, people, proposal, onSubmit, onCancel }: {
  entry: Entry; chores: Chore[]; people: Person[]; proposal?: boolean;
  onSubmit: (other: string, reason: string) => void; onCancel: () => void;
}) {
  const [weeks, setWeeks] = useState(8);
  const [candidates, setCandidates] = useState<Entry[] | null>(null);
  const [pick, setPick] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<unknown>(null);
  const from = today() < entry.date ? today() : entry.date;

  useEffect(() => {
    api.calendar(from, addDays(from, weeks * 7)).then((all) => setCandidates(all.filter((e) =>
      e.id !== entry.id && e.person_id !== entry.person_id && e.rule_id != null && !e.skipped && !e.outcome
      && !e.pending_swap)), setError);
  }, [from, weeks, entry.id, entry.person_id]);

  const label = (e: Entry) => {
    const c = chores.find((x) => x.id === e.chore_id);
    return `${dayLabel(e.date)} — ${c?.icon ?? ""} ${c?.title ?? "Chore"} — ${people.find((p) => p.id === e.person_id)?.name}`;
  };

  return (
    <form className="stack" onSubmit={(e: FormEvent) => { e.preventDefault(); onSubmit(pick, reason.trim()); }}>
      <label>Swap with
        <select value={pick} onChange={(e) => setPick(e.target.value)} required size={Math.min(8, Math.max(2, candidates?.length ?? 2))}>
          {candidates?.map((c) => <option key={c.id} value={c.id}>{label(c)}</option>)}
        </select>
      </label>
      {candidates && candidates.length === 0 && <p className="muted">Nothing of anyone else's in the next {weeks} weeks.</p>}
      <button type="button" className="ghost" onClick={() => setWeeks(weeks + 8)}>Look further ahead ({weeks + 8} weeks)</button>
      <label>Why<input value={reason} onChange={(e) => setReason(e.target.value)} required /></label>
      <ErrorNote error={error} />
      <div className="actions">
        {proposal && <p className="muted">They'll be asked to approve it; nothing changes until they do.</p>}
        <button type="submit" disabled={!pick || !reason.trim()}>{proposal ? "Ask them" : "Swap"}</button>
        <button type="button" className="ghost" onClick={onCancel}>Back</button>
      </div>
    </form>
  );
}
