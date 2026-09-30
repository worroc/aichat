import { useLive } from "../lib/live";
import { clockTime } from "../lib/time";

/** Alert events (join_code_reused, credential_leaked, ...) as dismissable banners. */
export function Toasts() {
  const { alerts, dismissAlert } = useLive();
  if (alerts.length === 0) return null;
  return (
    <div className="toasts">
      {alerts.map(({ id, event }) => (
        <div key={id} className="toast" role="alert">
          <strong>{String(event.data.alert ?? "alert")}</strong>
          <span>
            {event.data.name ? ` identity ${String(event.data.name)}` : ""} in namespace {event.ns ?? "?"} at{" "}
            {clockTime(event.time)}
          </span>
          <button className="link" onClick={() => dismissAlert(id)}>
            dismiss
          </button>
        </div>
      ))}
    </div>
  );
}
