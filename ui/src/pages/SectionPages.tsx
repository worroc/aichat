// Read-only namespace sections: agents, questions, read status, audit.
import { opsApi, channelLabel, type ChannelOut, type OpsIdentityOut } from "../api/client";
import { AgentTree } from "../components/AgentTree";
import { ErrorBox } from "../components/ErrorBox";
import { QuestionStatus } from "../components/MessageItem";
import { href } from "../lib/router";
import { fullTime, relativeTime } from "../lib/time";
import { useAsync } from "../lib/useAsync";
import { useNow } from "../lib/useNow";

interface SectionProps {
  ns: string;
  version: number;
}

export function AgentsSection({ agents, error }: { agents: OpsIdentityOut[] | null; error: unknown }) {
  const now = useNow();
  return (
    <div className="page">
      <h2>Agents</h2>
      <ErrorBox error={error} />
      {agents && <AgentTree agents={agents} now={now} />}
    </div>
  );
}

export function QuestionsSection({ ns, version }: SectionProps) {
  const { data, error } = useAsync(() => opsApi.questions(ns), [ns], version);
  return (
    <div className="page">
      <h2>Questions</h2>
      <ErrorBox error={error} />
      <table className="table">
        <thead>
          <tr>
            <th>time</th>
            <th>channel</th>
            <th>from</th>
            <th>to</th>
            <th>question</th>
            <th>status</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(data ?? []).map((q) => (
            <tr key={q.id}>
              <td title={fullTime(q.time)}>{relativeTime(q.time)}</td>
              <td>{q.channel}</td>
              <td>{q.sender}</td>
              <td>{q.to.join(", ") || "any"}</td>
              <td className="clip">{q.text}</td>
              <td>
                <QuestionStatus m={q} />
              </td>
              <td>
                <a href={href({ page: "thread", ns, threadId: q.thread_id })}>thread</a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {data && data.length === 0 && <p className="muted">no questions</p>}
    </div>
  );
}

export function PointersSection({ ns, version, channels }: SectionProps & { channels: ChannelOut[] | null }) {
  const { data, error } = useAsync(() => opsApi.pointers(ns), [ns], version);
  const byLabel = new Map((channels ?? []).map((c) => [channelLabel(c), c]));
  const groups = new Map<string, { reader: string; acked_seq: number }[]>();
  for (const p of data ?? []) {
    const list = groups.get(p.channel) ?? [];
    list.push(p);
    groups.set(p.channel, list);
  }
  return (
    <div className="page">
      <h2>Read status</h2>
      <ErrorBox error={error} />
      {[...groups.entries()].map(([label, readers]) => {
        const channel = byLabel.get(label);
        const last = channel?.last_seq ?? 0;
        return (
          <section key={label} className="card">
            <h3>
              {channel ? <a href={href({ page: "channel", ns, channelId: channel.id })}>{label}</a> : label}{" "}
              <span className="muted">last seq {last}</span>
            </h3>
            <table className="table">
              <tbody>
                {readers.map((r) => (
                  <tr key={r.reader}>
                    <td>{r.reader}</td>
                    <td className="bar-cell">
                      <progress max={Math.max(last, 1)} value={Math.min(r.acked_seq, last)} />
                    </td>
                    <td>
                      {r.acked_seq} / {last}
                      {last > r.acked_seq && <span className="unread"> {last - r.acked_seq} unread</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        );
      })}
      {data && data.length === 0 && <p className="muted">no reader has acked anything yet</p>}
    </div>
  );
}

export function AuditSection({ ns, version }: SectionProps) {
  const { data, error } = useAsync(() => opsApi.audit(ns), [ns], version);
  const rows = [...(data ?? [])].reverse();
  return (
    <div className="page">
      <h2>Audit</h2>
      <ErrorBox error={error} />
      <table className="table">
        <thead>
          <tr>
            <th>time</th>
            <th>actor</th>
            <th>action</th>
            <th>target</th>
            <th>detail</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((a, i) => (
            <tr key={i}>
              <td title={fullTime(a.ts)}>{relativeTime(a.ts)}</td>
              <td>{a.actor}</td>
              <td>
                <code>{a.action}</code>
              </td>
              <td>{a.target}</td>
              <td>{a.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
