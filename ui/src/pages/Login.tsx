import { useState } from "react";
import { opsApi } from "../api/client";
import { ErrorBox } from "../components/ErrorBox";

/** Asks for the operator key and checks it against GET /ops/namespaces. */
export function Login({ onLogin }: { onLogin: (key: string) => void }) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await opsApi.checkKey(key.trim());
      onLogin(key.trim());
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
          autoFocus
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="operator key"
          autoComplete="current-password"
        />
        <button type="submit" disabled={busy || !key.trim()}>
          {busy ? "Checking..." : "Sign in"}
        </button>
        <ErrorBox error={error} />
      </form>
    </div>
  );
}
