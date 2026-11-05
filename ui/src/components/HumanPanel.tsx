import type { HumanResponse } from "../api/client";
import { ErrorBox } from "./ErrorBox";

interface Props {
  name: string;
  human: HumanResponse | null;
  error: unknown;
  onChangeName: () => void;
}

/** Who the UI posts as in this namespace (the name from the login page). */
export function HumanPanel({ name, human, error, onChangeName }: Props) {
  return (
    <section className="human-panel">
      <h4>You</h4>
      <div className="row">
        <strong className="clip" title={name}>
          {name}
        </strong>
        <span className="grow" />
        <button className="link" onClick={onChangeName}>
          change name
        </button>
      </div>
      {!human && !error && <span className="muted small">joining...</span>}
      <ErrorBox error={error} />
    </section>
  );
}
