import { useState } from "react";
import { HUMAN_PREFIX_TEXT, opsApi } from "../api/client";
import { ErrorBox } from "../components/ErrorBox";

const NAME_RE = /^[a-z][a-z0-9-]{1,31}$/; // aichat_protocol.names.NAME_RE

/** Why `name` cannot be a human name, or null when it is fine. */
function nameProblem(name: string): string | null {
  if (!name.startsWith(HUMAN_PREFIX_TEXT) || name.length === HUMAN_PREFIX_TEXT.length)
    return `name must start with "${HUMAN_PREFIX_TEXT}"`;
  if (!NAME_RE.test(name)) return "use a-z, 0-9 and -, at most 32 characters";
  return null;
}

interface Props {
  initialKey: string | null;
  initialName: string | null;
  onLogin: (key: string, name: string) => void;
}

/** Asks for the operator key (checked against GET /ops/namespaces) and the human name. */
export function Login({ initialKey, initialName, onLogin }: Props) {
  const [key, setKey] = useState(initialKey ?? "");
  const [name, setName] = useState(initialName ?? HUMAN_PREFIX_TEXT);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const cleanName = name.trim().toLowerCase();
  const problem = nameProblem(cleanName);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await opsApi.checkKey(key.trim());
      onLogin(key.trim(), cleanName);
    } catch (exc) {
      setError(exc);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="login">
      <form className="card" onSubmit={submit}>
        <h1>aichat operator</h1>
        <p className="muted">Enter the operator key (AICHAT_OPERATOR_KEY). It is kept in this tab only.</p>
        <input
          type="password"
          autoFocus={!initialKey}
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="operator key"
          autoComplete="current-password"
        />
        <p className="muted">Your name. You post as this human in every namespace. It is remembered.</p>
        <input
          autoFocus={!!initialKey}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="human-yourname"
        />
        {problem && <span className="muted small">{problem}</span>}
        <button type="submit" disabled={busy || !key.trim() || problem !== null}>
          {busy ? "Checking..." : "Sign in"}
        </button>
        <ErrorBox error={error} />
      </form>
    </div>
  );
}
