import { useState } from "react";
import { opsApi, type CreateNamespaceResponse } from "../api/client";
import { CopyButton } from "../components/CopyButton";
import { ErrorBox } from "../components/ErrorBox";
import { useLive } from "../lib/live";
import { href } from "../lib/router";
import { relativeTime } from "../lib/time";
import { useAsync } from "../lib/useAsync";
import { useNow } from "../lib/useNow";

function CreateNamespace({ onCreated }: { onCreated: (r: CreateNamespaceResponse) => void }) {
  const [adminName, setAdminName] = useState("main");
  const [provider, setProvider] = useState("claude");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onCreated(await opsApi.createNamespace({ admin_name: adminName, provider }));
    } catch (exc) {
      setError(exc);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="card row wrap" onSubmit={submit}>
      <label>
        admin name <input value={adminName} onChange={(e) => setAdminName(e.target.value)} />
      </label>
      <label>
        provider <input value={provider} onChange={(e) => setProvider(e.target.value)} />
      </label>
      <button type="submit" disabled={busy || !adminName.trim()}>
        Create namespace
      </button>
      <ErrorBox error={error} onClose={() => setError(null)} />
    </form>
  );
}

/** The join code is shown once: the server never returns it again. */
function JoinCode({ created, onClose }: { created: CreateNamespaceResponse; onClose: () => void }) {
  return (
    <div className="card highlight">
      <p>
        Namespace <code>{created.ns_id}</code> created. Admin <strong>{created.admin_name}</strong> joins with this
        one-time code. It is shown only now.
      </p>
      <div className="row">
        <code className="secret">{created.join_code}</code>
        <CopyButton text={created.join_code} />
        <button className="link" onClick={onClose}>
          done
        </button>
      </div>
    </div>
  );
}

export function NamespacesPage() {
  const { globalVersion } = useLive();
  const now = useNow();
  const { data, error } = useAsync(() => opsApi.namespaces(), [], globalVersion);
  const [created, setCreated] = useState<CreateNamespaceResponse | null>(null);
  return (
    <div className="page">
      <h2>Namespaces</h2>
      {created ? <JoinCode created={created} onClose={() => setCreated(null)} /> : <CreateNamespace onCreated={setCreated} />}
      <ErrorBox error={error} />
      <table className="table">
        <thead>
          <tr>
            <th>id</th>
            <th>created</th>
            <th>identities</th>
            <th>rooms</th>
            <th>messages</th>
          </tr>
        </thead>
        <tbody>
          {(data ?? []).map((ns) => (
            <tr key={ns.id}>
              <td>
                <a href={href({ page: "ns", ns: ns.id, section: "agents" })}>
                  <code>{ns.id}</code>
                </a>
              </td>
              <td title={ns.created_at}>{relativeTime(ns.created_at, now)}</td>
              <td>{ns.identities}</td>
              <td>{ns.rooms}</td>
              <td>{ns.messages}</td>
            </tr>
          ))}
          {data && data.length === 0 && (
            <tr>
              <td colSpan={5} className="muted">
                no namespaces yet
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
