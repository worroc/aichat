import { useEffect, useRef, useState } from "react";
import { opsApi, type ChannelOut, type HumanResponse, type MessageOut, type OpsIdentityOut } from "../api/client";
import { Composer, type Target } from "../components/Composer";
import { ErrorBox } from "../components/ErrorBox";
import { MessageItem } from "../components/MessageItem";
import { ThreadView } from "../components/ThreadView";
import { useAsync } from "../lib/useAsync";

const PAGE = 100;
const DM_PREFIX = "dm:";

interface Props {
  ns: string;
  channelId: string;
  channel: ChannelOut | undefined;
  agents: OpsIdentityOut[];
  human: HumanResponse | null;
  version: number;
}

/** DM peer of the human, when the human is one side of this direct channel. */
function dmPeer(channelId: string, channel: ChannelOut | undefined, human: HumanResponse | null): string | null {
  if (!human) return null;
  const name = channel ? channel.name : channelId.startsWith(DM_PREFIX) ? channelId.slice(DM_PREFIX.length) : "";
  if (channel && channel.kind !== "direct") return null;
  const sides = name.split("|");
  if (sides.length !== 2 || !sides.includes(human.name)) return null;
  return sides[0] === human.name ? sides[1] : sides[0];
}

function composerTarget(channelId: string, channel: ChannelOut | undefined, human: HumanResponse | null): Target | null {
  if (!human) return null;
  if (channel?.kind === "room") return { kind: "room", room: channel.name, members: channel.members };
  const peer = dmPeer(channelId, channel, human);
  return peer ? { kind: "dm", peer } : null;
}

function Filters(props: {
  senders: string[];
  sender: string;
  kind: string;
  setSender: (s: string) => void;
  setKind: (k: string) => void;
  text: string;
  setText: (t: string) => void;
  newestFirst: boolean;
  setNewestFirst: (v: boolean) => void;
  shown: number;
  total: number;
}) {
  return (
    <div className="row wrap filters">
      <input placeholder="filter by text" value={props.text} onChange={(e) => props.setText(e.target.value)} />
      <label>
        sender{" "}
        <select value={props.sender} onChange={(e) => props.setSender(e.target.value)}>
          <option value="">all</option>
          {props.senders.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
      <label>
        kind{" "}
        <select value={props.kind} onChange={(e) => props.setKind(e.target.value)}>
          <option value="">all</option>
          <option value="question">question</option>
          <option value="information">information</option>
          <option value="answer">answer</option>
        </select>
      </label>
      <button className="link" onClick={() => props.setNewestFirst(!props.newestFirst)}>
        {props.newestFirst ? "newest first ↓" : "oldest first ↑"}
      </button>
      <span className="muted small">
        {props.shown} of {props.total}
      </span>
    </div>
  );
}

function merge(older: MessageOut[], latest: MessageOut[]): MessageOut[] {
  const byId = new Map<string, MessageOut>();
  for (const m of [...older, ...latest]) byId.set(m.id, m);
  return [...byId.values()].sort((a, b) => a.seq - b.seq);
}

export function ChannelPage({ ns, channelId, channel, agents, human, version }: Props) {
  const [sender, setSender] = useState("");
  const [kind, setKind] = useState("");
  const [text, setText] = useState("");
  const [newestFirst, setNewestFirst] = useState(false); // a chat reads oldest first
  const [older, setOlder] = useState<{ messages: MessageOut[]; hasMore: boolean } | null>(null);
  const [olderError, setOlderError] = useState<unknown>(null);
  const [threadId, setThreadId] = useState<string | null>(null);

  const filter = { channel_id: channelId, sender, kind };
  const latest = useAsync(() => opsApi.messages(ns, { ...filter, limit: PAGE }), [ns, channelId, sender, kind], version);

  // Older pages belong to one filter; drop them when the filter changes.
  const filterKey = JSON.stringify([ns, channelId, sender, kind]);
  const [olderKey, setOlderKey] = useState(filterKey);
  if (olderKey !== filterKey) {
    setOlderKey(filterKey);
    setOlder(null);
    setThreadId(null);
  }

  const loaded = merge(older?.messages ?? [], latest.data?.messages ?? []);
  const needle = text.trim().toLowerCase();
  const matching = loaded.filter((m) => m.text.toLowerCase().includes(needle));
  const messages = newestFirst ? [...matching].reverse() : matching;
  const hasMore = older ? older.hasMore : (latest.data?.has_more ?? false);

  const loadOlder = async () => {
    if (loaded.length === 0) return;
    setOlderError(null);
    try {
      const page = await opsApi.messages(ns, { ...filter, before_seq: loaded[0].seq, limit: PAGE });
      setOlder({ messages: [...page.messages, ...(older?.messages ?? [])], hasMore: page.has_more });
    } catch (exc) {
      setOlderError(exc);
    }
  };

  // Oldest first: follow new messages at the bottom, but not when older pages were loaded.
  const end = useRef<HTMLDivElement>(null);
  const newest = latest.data?.messages.at(-1)?.id;
  useEffect(() => {
    if (!older && !newestFirst) end.current?.scrollIntoView({ block: "end" });
  }, [newest, older, newestFirst]);

  const senders = [...new Set([...agents.map((a) => a.name), ...(channel?.members ?? [])])].sort();
  const target = composerTarget(channelId, channel, human);
  const title = channel ? (channel.kind === "room" ? "#" + channel.name : channel.name.replace("|", " ↔ ")) : channelId;

  return (
    <div className={`channel ${threadId ? "with-thread" : ""}`}>
      <div className="channel-main">
        <header className="channel-head">
          <h2>{title}</h2>
          {channel && (
            <span className="muted">
              {channel.members.length} members: {channel.members.join(", ")} &middot; history {channel.history} &middot; last
              seq {channel.last_seq}
            </span>
          )}
          <Filters
            senders={senders}
            sender={sender}
            kind={kind}
            setSender={setSender}
            setKind={setKind}
            text={text}
            setText={setText}
            newestFirst={newestFirst}
            setNewestFirst={setNewestFirst}
            shown={messages.length}
            total={loaded.length}
          />
        </header>
        <ErrorBox error={latest.error} />
        <ErrorBox error={olderError} onClose={() => setOlderError(null)} />
        {hasMore && !newestFirst && (
          <button className="link" onClick={loadOlder}>
            load older messages
          </button>
        )}
        <ul className="messages">
          {messages.map((m) => (
            <MessageItem key={m.id} m={m} human={human} onOpenThread={(msg) => setThreadId(msg.thread_id)} />
          ))}
        </ul>
        {!channel && !target && <p className="muted">channel not found</p>}
        {channel && loaded.length === 0 && !latest.loading && <p className="muted">no messages</p>}
        {loaded.length > 0 && messages.length === 0 && <p className="muted">no message matches the filter</p>}
        {target && human && <Composer human={human} target={target} onPosted={latest.reload} />}
        {hasMore && newestFirst && (
          <button className="link" onClick={loadOlder}>
            load older messages
          </button>
        )}
        <div ref={end} />
      </div>
      {threadId && (
        <ThreadView ns={ns} threadId={threadId} version={version} human={human} onClose={() => setThreadId(null)} />
      )}
    </div>
  );
}
