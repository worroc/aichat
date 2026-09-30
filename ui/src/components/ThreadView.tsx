import { opsApi, type HumanResponse } from "../api/client";
import { useAsync } from "../lib/useAsync";
import { ErrorBox } from "./ErrorBox";
import { MessageItem } from "./MessageItem";

interface Props {
  ns: string;
  threadId: string;
  version: number;
  human: HumanResponse | null;
  onClose?: () => void;
}

/** All messages of one thread (a question and its answers, or a reply chain). */
export function ThreadView({ ns, threadId, version, human, onClose }: Props) {
  const { data, error, reload } = useAsync(() => opsApi.messages(ns, { thread_id: threadId, limit: 200 }), [ns, threadId], version);
  return (
    <div className="thread">
      <header className="row">
        <h3>Thread</h3>
        <code className="muted small">{threadId}</code>
        <span className="grow" />
        {onClose && (
          <button className="link" onClick={onClose}>
            close
          </button>
        )}
      </header>
      <ErrorBox error={error} />
      <ul className="messages">
        {(data?.messages ?? []).map((m) => (
          <MessageItem key={m.id} m={m} human={human} showChannel />
        ))}
      </ul>
      {data && data.messages.length === 0 && <p className="muted">empty thread</p>}
      <button className="link small" onClick={reload}>
        refresh
      </button>
    </div>
  );
}
