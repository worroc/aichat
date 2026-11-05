import { useState } from "react";
import type { HumanResponse, MessageMetadata, PostKind, PostMessageRequest } from "../api/client";
import { humanApi } from "../api/client";
import { ErrorBox } from "./ErrorBox";

export type Target = { kind: "room"; room: string; members: string[] } | { kind: "dm"; peer: string };

const KINDS: [PostKind, string][] = [
  ["question", "question"],
  ["information", "info"],
];

/** Comma or space separated tags, each kept once. */
function parseTags(raw: string): string[] {
  return [...new Set(raw.split(/[\s,]+/).filter(Boolean))];
}

const DEFAULT_BG = "#fff3b0";
const DEFAULT_FG = "#000000";

function ColorPick(props: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="color-pick" title={`${props.label} color`}>
      {props.label} <input type="color" value={props.value} onChange={(e) => props.onChange(e.target.value)} />
    </label>
  );
}

interface Props {
  human: HumanResponse;
  target: Target;
  onPosted: () => void;
}

/** Posts as the human: a room message or a DM, information or question. */
export function Composer({ human, target, onPosted }: Props) {
  const [text, setText] = useState("");
  const [kind, setKind] = useState<PostKind>("question");
  const [maxAnswers, setMaxAnswers] = useState("");
  const [to, setTo] = useState<Set<string>>(new Set());
  const [paint, setPaint] = useState(false); // off: the message gets no colors
  const [bg, setBg] = useState(DEFAULT_BG);
  const [fg, setFg] = useState(DEFAULT_FG);
  const [tags, setTags] = useState("");
  const [pinned, setPinned] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const others = target.kind === "room" ? target.members.filter((n) => n !== human.name) : [];

  const buildRequest = (): PostMessageRequest => {
    const req: PostMessageRequest = { text, kind };
    if (kind === "question" && target.kind === "room" && maxAnswers) req.max_agents_answers = Number(maxAnswers);
    if (target.kind === "room" && to.size > 0) req.to = [...to];
    const meta: MessageMetadata = { tags: parseTags(tags), pinned };
    if (paint) Object.assign(meta, { bg_color: bg, fg_color: fg });
    if (paint || meta.tags?.length || pinned) req.metadata = meta;
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
      setPinned(false);
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
        autoFocus
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        style={paint ? { background: bg, color: fg } : undefined}
        placeholder={`Write as ${human.name} to ${target.kind === "room" ? "#" + target.room : "@" + target.peer}`}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void submit(e);
        }}
      />
      <div className="row wrap">
        <span className="toggle">
          {KINDS.map(([k, label]) => (
            <button key={k} type="button" className={kind === k ? "on" : undefined} onClick={() => setKind(k)}>
              {label}
            </button>
          ))}
        </span>
        {kind === "question" && target.kind === "room" && (
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
        <label title="color the message">
          <input type="checkbox" checked={paint} onChange={(e) => setPaint(e.target.checked)} /> paint
        </label>
        {paint && <ColorPick label="bg" value={bg} onChange={setBg} />}
        {paint && <ColorPick label="text" value={fg} onChange={setFg} />}
        <input
          className="tags-input"
          value={tags}
          onChange={(e) => setTags(e.target.value)}
          placeholder="tags"
          title="tags, separated by commas or spaces"
        />
        <label title="show this message at the top of the chat">
          <input type="checkbox" checked={pinned} onChange={(e) => setPinned(e.target.checked)} /> pin
        </label>
        <span className="grow" />
        <button type="submit" disabled={busy || !text.trim()}>
          {busy ? "Sending..." : "Send"}
        </button>
      </div>
      <ErrorBox error={error} onClose={() => setError(null)} />
    </form>
  );
}
