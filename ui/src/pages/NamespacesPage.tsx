import { useState } from "react";
import { opsApi, type CreateNamespaceResponse, type NamespaceOut } from "../api/client";
import { CopyButton } from "../components/CopyButton";
import { ErrorBox } from "../components/ErrorBox";
import { useLive } from "../lib/live";
import { href } from "../lib/router";
import { relativeTime } from "../lib/time";
import { useAsync } from "../lib/useAsync";
import { useNow } from "../lib/useNow";

function CreateNamespace({ onCreated }: { onCreated: (r: CreateNamespaceResponse) => void }) {
  const [title, setTitle] = useState("");
  const [adminName, setAdminName] = useState("main");
  const [provider, setProvider] = useState("claude");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onCreated(await opsApi.createNamespace({ title, admin_name: adminName, provider }));
    } catch (exc) {
      setError(exc);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="card row wrap" onSubmit={submit}>
      <label>
        title <input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
      </label>
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
        Namespace {created.title && <strong>{created.title}</strong>} <code>{created.ns_id}</code> created. Admin <strong>{created.admin_name}</strong> joins with this
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

/** Deletes the namespace after a confirm; the list refreshes from the live event. */
function DeleteButton({ ns, onError }: { ns: NamespaceOut; onError: (exc: unknown) => void }) {
  const [busy, setBusy] = useState(false);
  const remove = async () => {
    const name = ns.title ? `"${ns.title}" (${ns.id})` : ns.id;
    if (!window.confirm(`Delete namespace ${name} with all its agents, rooms and messages? This cannot be undone.`)) return;
    setBusy(true);
    try {
      await opsApi.deleteNamespace(ns.id);
    } catch (exc) {
      onError(exc);
    } finally {
      setBusy(false);
    }
  };
  return (
    <button className="danger small" disabled={busy} onClick={remove}>
      Delete
    </button>
  );
}

export function NamespacesPage() {
  const { globalVersion } = useLive();
  const now = useNow();
  const { data, error } = useAsync(() => opsApi.namespaces(), [], globalVersion);
  const [created, setCreated] = useState<CreateNamespaceResponse | null>(null);
  const [deleteError, setDeleteError] = useState<unknown>(null);
  return (
    <div className="page">
      <h2>Namespaces</h2>
      {created ? <JoinCode created={created} onClose={() => setCreated(null)} /> : <CreateNamespace onCreated={setCreated} />}
      <ErrorBox error={error} />
      <ErrorBox error={deleteError} onClose={() => setDeleteError(null)} />
      <table className="table">
        <thead>
          <tr>
            <th>title</th>
            <th>id</th>
            <th>created</th>
            <th>identities</th>
            <th>rooms</th>
            <th>messages</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {(data ?? []).map((ns) => (
            <tr key={ns.id}>
              <td>{ns.title || <span className="muted">-</span>}</td>
              <td>
                <a href={href({ page: "ns", ns: ns.id, section: "agents" })}>
                  <code>{ns.id}</code>
                </a>
              </td>
              <td title={ns.created_at}>{relativeTime(ns.created_at, now)}</td>
              <td>{ns.identities}</td>
              <td>{ns.rooms}</td>
              <td>{ns.messages}</td>
              <td>
                <DeleteButton ns={ns} onError={setDeleteError} />
              </td>
            </tr>
          ))}
          {data && data.length === 0 && (
            <tr>
              <td colSpan={7} className="muted">
                no namespaces yet
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
