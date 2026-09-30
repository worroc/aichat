import { useState } from "react";
import type { HumanResponse, MessageOut, Ref } from "../api/client";
import { humanApi } from "../api/client";
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

function RefView({ r }: { r: Ref }) {
  if (r.type === "url") {
    const href = safeUrl(r.url);
    return href ? (
      <a href={href} target="_blank" rel="noopener noreferrer">
        {r.url}
      </a>
    ) : (
      <span>{r.url}</span>
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
}

export function MessageItem({ m, human, onOpenThread, showChannel }: Props) {
  const [answering, setAnswering] = useState(false);
  const canAnswer = human && m.kind === "question" && !m.closed && m.sender !== human.name;
  return (
    <li className={`msg msg-${m.kind}`} onClick={() => onOpenThread?.(m)} title={onOpenThread ? "Open thread" : undefined}>
      <div className="msg-head">
        <strong className="sender">{m.sender}</strong>
        <Badge tone={`role-${m.sender_role}`}>{m.sender_role}</Badge>
        <Badge tone={`kind-${m.kind}`}>{m.kind}</Badge>
        <QuestionStatus m={m} />
        {showChannel && <span className="muted">{m.channel}</span>}
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
              <RefView r={r} />
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
