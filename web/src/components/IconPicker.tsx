import { useEffect, useRef, useState } from "react";
import { ICON_GROUPS } from "../icons";

/** The chore icon: click to choose from a grid, or type any emoji. No length
 *  limit -- emoji like 🐕‍🦺 are several characters long, and a cap cut them
 *  in half (that was the vanishing-icon bug). */
export function IconPicker({ value, onChange }: { value: string; onChange: (icon: string) => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);

  return (
    <div className="iconpick" ref={box}>
      <span className="label">Icon</span>
      <div className="iconfield">
        <button type="button" className="iconbtn" aria-label="Choose an icon" aria-expanded={open} onClick={() => setOpen(!open)}>
          {value || "＋"}
        </button>
        <input aria-label="Icon (type any emoji)" value={value} onChange={(e) => onChange(e.target.value.trim())}
          placeholder="or type one" />
      </div>
      {open && (
        <div className="iconpop card" role="listbox" aria-label="Icons">
          {ICON_GROUPS.map((g) => (
            <div key={g.name}>
              <h4>{g.name}</h4>
              <div className="icongrid">
                {g.icons.map(({ icon, label }) => (
                  <button type="button" key={icon} role="option" aria-selected={icon === value} aria-label={label}
                    title={label} onClick={() => { onChange(icon); setOpen(false); }}>{icon}</button>
                ))}
              </div>
            </div>
          ))}
          {value && <button type="button" className="ghost" onClick={() => { onChange(""); setOpen(false); }}>No icon</button>}
        </div>
      )}
    </div>
  );
}
