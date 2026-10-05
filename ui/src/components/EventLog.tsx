import { useState } from "react";
import { useLive } from "../lib/live";
import { clockTime } from "../lib/time";
import { useResizable } from "../lib/useResizable";

const SHOWN = 80;

/** Small panel with the latest events from the operator stream. */
export function EventLog({ ns }: { ns?: string }) {
  const { events, status, statusDetail } = useLive();
  const [open, setOpen] = useState(true);
  const [onlyNs, setOnlyNs] = useState(true);
  const { width, onPointerDown } = useResizable({ storageKey: "aichat.eventLogWidth", initial: 340, min: 220, maxShare: 0.7 });
  const shown = events.filter((e) => !ns || !onlyNs || e.ns === ns).slice(-SHOWN).reverse();
  return (
    <aside className={`event-log ${open ? "open" : "closed"}`} style={open ? { width } : undefined}>
      {open && (
        <div
          className="resize-handle"
          role="separator"
          aria-orientation="vertical"
          title="Drag to resize"
          onPointerDown={onPointerDown}
        />
      )}
      <header>
        <button className="link" onClick={() => setOpen(!open)}>
          {open ? "Hide" : "Show"} events
        </button>
        <span className={`dot dot-${status}`} title={statusDetail ?? status} />
        <span className="muted">{status}</span>
        {ns && open && (
          <label className="muted small">
            <input type="checkbox" checked={onlyNs} onChange={(e) => setOnlyNs(e.target.checked)} /> this namespace
          </label>
        )}
      </header>
      {open && (
        <ol>
          {shown.map((e) => (
            <li key={e.seq}>
              <span className="muted">{clockTime(e.time)}</span> <span className={`ev ev-${e.type}`}>{e.type}</span>{" "}
              <code>{JSON.stringify(e.data)}</code>
            </li>
          ))}
          {shown.length === 0 && <li className="muted">no events yet</li>}
        </ol>
      )}
    </aside>
  );
}
