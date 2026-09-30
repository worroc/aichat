import { useState } from "react";
import { HUMAN_PREFIX_TEXT, opsApi, type HumanResponse, type OpsIdentityOut } from "../api/client";
import { navigate } from "../lib/router";
import { ErrorBox } from "./ErrorBox";

interface Props {
  ns: string;
  human: HumanResponse | null;
  setHuman: (h: HumanResponse | null) => void;
  agents: OpsIdentityOut[];
}

function ActAsHuman({ ns, setHuman }: { ns: string; setHuman: (h: HumanResponse) => void }) {
  const [name, setName] = useState(HUMAN_PREFIX_TEXT);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const valid = name.startsWith(HUMAN_PREFIX_TEXT) && name.length > HUMAN_PREFIX_TEXT.length;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setHuman(await opsApi.createHuman(ns, name.trim()));
    } catch (exc) {
      setError(exc);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="stack">
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="human-yourname" />
      <button type="submit" disabled={busy || !valid}>
        Act as human
      </button>
      {!valid && <span className="muted small">name must start with "{HUMAN_PREFIX_TEXT}"</span>}
      <ErrorBox error={error} onClose={() => setError(null)} />
    </form>
  );
}

function NewDm({ ns, human, agents }: { ns: string; human: HumanResponse; agents: OpsIdentityOut[] }) {
  const [peer, setPeer] = useState("");
  const peers = agents.filter((a) => a.name !== human.name && a.state !== "revoked");
  const open = () => {
    if (!peer) return;
    const id = "dm:" + [human.name, peer].sort().join("|");
    navigate({ page: "channel", ns, channelId: id });
    setPeer("");
  };
  return (
    <div className="row">
      <select value={peer} onChange={(e) => setPeer(e.target.value)}>
        <option value="">New DM with...</option>
        {peers.map((a) => (
          <option key={a.name} value={a.name}>
            {a.name}
          </option>
        ))}
      </select>
      <button onClick={open} disabled={!peer}>
        Open
      </button>
    </div>
  );
}

/** "Act as human": gets a human token for this namespace and keeps it in sessionStorage. */
export function HumanPanel({ ns, human, setHuman, agents }: Props) {
  return (
    <section className="human-panel">
      <h4>Human</h4>
      {human ? (
        <div className="stack">
          <div className="row">
            <strong>{human.name}</strong>
            <span className="grow" />
            <button className="link" onClick={() => setHuman(null)}>
              stop
            </button>
          </div>
          <NewDm ns={ns} human={human} agents={agents} />
        </div>
      ) : (
        <ActAsHuman ns={ns} setHuman={setHuman} />
      )}
    </section>
  );
}
