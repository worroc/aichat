import { useState } from "react";
import type { HumanResponse, PostKind, PostMessageRequest } from "../api/client";
import { humanApi } from "../api/client";
import { ErrorBox } from "./ErrorBox";

export type Target = { kind: "room"; room: string; members: string[] } | { kind: "dm"; peer: string };

interface Props {
  human: HumanResponse;
  target: Target;
  onPosted: () => void;
}

/** Posts as the human: a room message or a DM, information or question. */
export function Composer({ human, target, onPosted }: Props) {
  const [text, setText] = useState("");
  const [kind, setKind] = useState<PostKind>("information");
  const [maxAnswers, setMaxAnswers] = useState("");
  const [to, setTo] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const others = target.kind === "room" ? target.members.filter((n) => n !== human.name) : [];

  const buildRequest = (): PostMessageRequest => {
    const req: PostMessageRequest = { text, kind };
    if (kind === "question" && maxAnswers) req.max_agents_answers = Number(maxAnswers);
    if (target.kind === "room" && to.size > 0) req.to = [...to];
    return req;
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const api = humanApi(human.token);
      if (target.kind === "room") await api.postRoom(target.room, buildRequest());
      else await api.postDm(target.peer, buildRequest());
      setText("");
      onPosted();
    } catch (exc) {
      setError(exc);
    } finally {
      setBusy(false);
    }
  };

  const toggle = (name: string) => {
    const next = new Set(to);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setTo(next);
  };

  return (
    <form className="composer" onSubmit={submit}>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        placeholder={`Write as ${human.name} to ${target.kind === "room" ? "#" + target.room : "@" + target.peer}`}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void submit(e);
        }}
      />
      <div className="row wrap">
        <select value={kind} onChange={(e) => setKind(e.target.value as PostKind)}>
          <option value="information">information</option>
          <option value="question">question</option>
        </select>
        {kind === "question" && (
          <label>
            max answers{" "}
            <input
              type="number"
              min={1}
              value={maxAnswers}
              onChange={(e) => setMaxAnswers(e.target.value)}
              placeholder="1"
              className="narrow"
            />
          </label>
        )}
        {others.length > 0 && (
          <details className="to-picker">
            <summary>to: {to.size ? [...to].join(", ") : "everyone"}</summary>
            {others.map((n) => (
              <label key={n}>
                <input type="checkbox" checked={to.has(n)} onChange={() => toggle(n)} /> {n}
              </label>
            ))}
          </details>
        )}
        <span className="grow" />
        <button type="submit" disabled={busy || !text.trim()}>
          {busy ? "Sending..." : "Send"}
        </button>
      </div>
      <ErrorBox error={error} onClose={() => setError(null)} />
    </form>
  );
}
