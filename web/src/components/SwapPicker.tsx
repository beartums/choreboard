import { useState } from "react";
import { dayLabel } from "../dates";

/** One chore someone else could swap with. */
export interface Candidate { key: string; date: string; person: string; chore: string; window?: string }

type By = "chore" | "person";

/** Pick the other side of a swap in three small steps, the same in the app
 *  and the nav widget: by chore or by person; which one; then the other; then
 *  the date. Each list shows only what still has dates behind it. */
export function SwapPicker({ candidates, value, onChange }: {
  candidates: Candidate[]; value: string; onChange: (key: string) => void;
}) {
  const [by, setBy] = useState<By>("person");
  const [first, setFirst] = useState<string | null>(null);
  const [second, setSecond] = useState<string | null>(null);
  const firstOf = (c: Candidate) => (by === "person" ? c.person : c.chore);
  const secondOf = (c: Candidate) => (by === "person" ? c.chore : c.person);
  const uniq = (xs: string[]) => [...new Set(xs)].sort((a, b) => a.localeCompare(b));
  const firsts = uniq(candidates.map(firstOf));
  const afterFirst = candidates.filter((c) => firstOf(c) === first);
  const seconds = uniq(afterFirst.map(secondOf));
  const dates = afterFirst.filter((c) => secondOf(c) === second).sort((a, b) => a.date.localeCompare(b.date));

  const chips = (label: string, items: string[], picked: string | null, pick: (x: string) => void) => (
    <div className="pick-step" role="group" aria-label={label}>
      <span className="muted">{label}</span>
      {items.map((x) => (
        <button type="button" key={x} className={`chip${picked === x ? " on" : ""}`} aria-pressed={picked === x}
          onClick={() => pick(x)}>{x}</button>
      ))}
    </div>
  );

  if (!candidates.length) return <p className="muted">Nothing of anyone else's to swap with in this range.</p>;
  return (
    <div className="stack swap-picker">
      <div className="pick-step" role="group" aria-label="Swap by">
        <span className="muted">Find by</span>
        {(["person", "chore"] as By[]).map((b) => (
          <button type="button" key={b} className={`chip${by === b ? " on" : ""}`} aria-pressed={by === b}
            onClick={() => { setBy(b); setFirst(null); setSecond(null); onChange(""); }}>{b === "person" ? "Person" : "Chore"}</button>
        ))}
      </div>
      {chips(by === "person" ? "Whose" : "Which chore", firsts, first, (x) => { setFirst(x); setSecond(null); onChange(""); })}
      {first && chips(by === "person" ? "Which chore" : "Whose", seconds, second, (x) => { setSecond(x); onChange(""); })}
      {first && second && (
        <div className="pick-step" role="radiogroup" aria-label="Which day">
          <span className="muted">Which day</span>
          {dates.map((c) => (
            <button type="button" role="radio" key={c.key} aria-checked={value === c.key}
              className={`chip${value === c.key ? " on" : ""}`} onClick={() => onChange(c.key)}>
              {dayLabel(c.date)}{c.window ? ` · ${c.window}` : ""}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
