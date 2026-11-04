import { useState } from "react";
import type { HumanResponse, MessageOut, Ref } from "../api/client";
import { humanApi, opsApi } from "../api/client";
import { useRoute } from "../lib/router";
import { fullTime, clockTime } from "../lib/time";
import { Badge } from "./Badge";
import { ErrorBox } from "./ErrorBox";

/** Only http(s) links become anchors; anything else is shown as text. */
function safeUrl(url: string | null): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
  } catch {
    return null;
  }
}

/** Fetches the file with the operator key, then hands it to the browser as a download. */
async function saveFile(ns: string, messageId: string, r: Ref): Promise<void> {
  const data = await opsApi.file(ns, messageId, r.id ?? "");
  const url = URL.createObjectURL(data);
  const link = document.createElement("a");
  link.href = url;
  link.download = r.name ?? "file";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function FileRef({ r, messageId }: { r: Ref; messageId: string }) {
  const route = useRoute();
  const ns = "ns" in route ? route.ns : null;
  const [error, setError] = useState<unknown>(null);
  return (
    <span title={r.sha256 ? `sha256 ${r.sha256}` : ""}>
      file{" "}
      {ns ? (
        <button type="button" className="link" onClick={() => saveFile(ns, messageId, r).catch(setError)}>
          {r.name}
        </button>
      ) : (
        r.name
      )}{" "}
      <span className="muted">({r.size} bytes)</span>
      <ErrorBox error={error} onClose={() => setError(null)} />
    </span>
  );
}

function RefView({ r, messageId }: { r: Ref; messageId: string }) {
  if (r.type === "blob") return <FileRef r={r} messageId={messageId} />;
  if (r.type === "url") {
    const href = safeUrl(r.url);
    return href ? (
      <a href={href} target="_blank" rel="noopener noreferrer">
        {r.url}
      </a>
    ) : (
      <span>
        {r.url}
        {r.machine && <span className="muted"> (on {r.machine})</span>}
      </span>
    );
  }
  const parts = [r.repo, r.branch && `@${r.branch}`, r.commit && `#${r.commit.slice(0, 10)}`, r.path && `:${r.path}`];
  return <span>git {parts.filter(Boolean).join(" ")}</span>;
}

export function QuestionStatus({ m }: { m: MessageOut }) {
  if (m.kind !== "question") return null;
  const count = `${m.answers_count ?? 0}/${m.max_agents_answers ?? 1} answers`;
  return m.closed ? (
    <Badge tone="closed" title={m.closed_reason ?? ""}>
      closed: {m.closed_reason} ({count})
    </Badge>
  ) : (
    <Badge tone="open">open ({count})</Badge>
  );
}

function AnswerForm({ question, human, onDone }: { question: MessageOut; human: HumanResponse; onDone: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await humanApi(human.token).answer(question.id, text);
      setText("");
      onDone();
    } catch (exc) {
      setError(exc);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="answer-form" onSubmit={submit} onClick={(e) => e.stopPropagation()}>
      <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder={`Answer as ${human.name}`} rows={2} />
      <div className="row">
        <button type="submit" disabled={busy || !text.trim()}>
          Send answer
        </button>
        <button type="button" className="link" onClick={onDone}>
          cancel
        </button>
      </div>
      <ErrorBox error={error} onClose={() => setError(null)} />
    </form>
  );
}

interface Props {
  m: MessageOut;
  human: HumanResponse | null;
  onOpenThread?: (m: MessageOut) => void;
  showChannel?: boolean;
  /** When set, the head reads "sender → destination" instead of sender + channel. */
  destination?: string;
  /** Shows a mark at the start of the head: a new message or a response. */
  marker?: "new" | "reply";
}

export function MessageItem({ m, human, onOpenThread, showChannel, destination, marker }: Props) {
  const [answering, setAnswering] = useState(false);
  const canAnswer = human && m.kind === "question" && !m.closed && m.sender !== human.name;
  return (
    <li className={`msg msg-${m.kind}`} onClick={() => onOpenThread?.(m)} title={onOpenThread ? "Open thread" : undefined}>
      <div className="msg-head">
        {marker && (
          <span className={`marker marker-${marker}`} title={marker === "reply" ? "response" : "new message"}>
            {marker === "reply" ? "↩" : "✉"}
          </span>
        )}
        <strong className="sender" title={m.sender_machine ? `on ${m.sender_machine}` : undefined}>
          {m.sender}
          {destination && <> &rarr; {destination}</>}
        </strong>
        <Badge tone={`role-${m.sender_role}`}>{m.sender_role}</Badge>
        <Badge tone={`kind-${m.kind}`}>{m.kind}</Badge>
        <QuestionStatus m={m} />
        {showChannel && !destination && <span className="muted">{m.channel}</span>}
        {m.to.length > 0 && <span className="muted">to {m.to.join(", ")}</span>}
        <span className="muted" title={fullTime(m.time)}>
          {clockTime(m.time)} #{m.seq}
        </span>
        {m.amended_at && <span className="muted">(amended)</span>}
      </div>
      {/* Rendered as a text node: never as HTML. */}
      <div className="msg-text">{m.text}</div>
      {m.refs.length > 0 && (
        <ul className="refs" onClick={(e) => e.stopPropagation()}>
          {m.refs.map((r, i) => (
            <li key={i}>
              <RefView r={r} messageId={m.id} />
            </li>
          ))}
        </ul>
      )}
      {canAnswer && !answering && (
        <button
          className="small"
          onClick={(e) => {
            e.stopPropagation();
            setAnswering(true);
          }}
        >
          Answer
        </button>
      )}
      {canAnswer && answering && human && <AnswerForm question={m} human={human} onDone={() => setAnswering(false)} />}
    </li>
  );
}
