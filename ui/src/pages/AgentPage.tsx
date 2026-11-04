// One agent: its details, the channels it talks in, and its messages.
import { useState } from "react";
import {
  channelLabel,
  opsApi,
  type ChannelOut,
  type HumanResponse,
  type MessageOut,
  type OpsIdentityOut,
} from "../api/client";
import { Badge } from "../components/Badge";
import { ErrorBox } from "../components/ErrorBox";
import { MessageItem } from "../components/MessageItem";
import { ThreadView } from "../components/ThreadView";
import { href, navigate } from "../lib/router";
import { fullTime, relativeTime } from "../lib/time";
import { useAsync } from "../lib/useAsync";
import { useNow } from "../lib/useNow";

const PAGE = 200; // the hub's max_limit

type Direction = "from" | "to" | "all";

interface Props {
  ns: string;
  name: string;
  agents: OpsIdentityOut[];
  channels: ChannelOut[];
  human: HumanResponse | null;
  version: number;
}

/** The other side of a direct channel "a|b", or null when `name` is not a side. */
function otherSide(dmName: string, name: string): string | null {
  const sides = dmName.split("|");
  if (sides.length !== 2 || !sides.includes(name)) return null;
  return sides[0] === name ? sides[1] : sides[0];
}

/** Where a message went: "#room", or the DM peer of its sender. */
function destination(m: MessageOut): string {
  if (m.channel.startsWith("#")) return m.channel;
  return otherSide(m.channel, m.sender) ?? m.channel;
}

function agentChannels(channels: ChannelOut[], name: string): ChannelOut[] {
  const dms = channels.filter(
    (c) => c.kind === "direct" && otherSide(c.name, name) !== null,
  );
  const rooms = channels.filter(
    (c) => c.kind === "room" && c.members.includes(name),
  );
  return [...dms, ...rooms];
}

/** Every message of one channel, following the pages back to the start. */
async function loadChannel(
  ns: string,
  channelId: string,
): Promise<MessageOut[]> {
  const out: MessageOut[] = [];
  let before: number | undefined;
  for (;;) {
    const page = await opsApi.messages(ns, {
      channel_id: channelId,
      limit: PAGE,
      before_seq: before,
    });
    out.push(...page.messages);
    if (!page.has_more || page.messages.length === 0) return out;
    before = Math.min(...page.messages.map((m) => m.seq));
  }
}

async function loadAll(
  ns: string,
  channels: ChannelOut[],
): Promise<MessageOut[]> {
  const lists = await Promise.all(channels.map((c) => loadChannel(ns, c.id)));
  return lists.flat();
}

/** Answers and replies continue a thread; everything else starts one. */
function isReply(m: MessageOut): boolean {
  return m.kind === "answer" || m.reply_to !== null;
}

/** Sent to `name`: any DM it is a side of, or a room post that names it in `to`. */
function isFor(m: MessageOut, name: string): boolean {
  if (!m.channel.startsWith("#")) return true;
  return m.to.includes(name);
}

/**
 * The message involves `name`: it sent it, it was sent to it, or it answers
 * one of its messages (`own` holds the ids of messages `name` sent).
 */
function involves(m: MessageOut, name: string, own: Set<string>): boolean {
  if (m.sender === name || isFor(m, name)) return true;
  return m.reply_to !== null && own.has(m.reply_to);
}

function matchesDirection(
  m: MessageOut,
  name: string,
  direction: Direction,
  own: Set<string>,
): boolean {
  if (direction === "all") return involves(m, name, own);
  if (isReply(m)) return false;
  return direction === "from"
    ? m.sender === name
    : m.sender !== name && isFor(m, name);
}

interface MessageNode {
  m: MessageOut;
  replies: MessageNode[];
}

/**
 * Puts each response under the message it answers. A response whose request
 * is not in the list stays at the top level. Top level keeps `ordered`; replies
 * read oldest first.
 */
function buildTree(ordered: MessageOut[]): MessageNode[] {
  const nodes = new Map(
    ordered.map((m) => [m.id, { m, replies: [] as MessageNode[] }]),
  );
  const roots: MessageNode[] = [];
  for (const node of nodes.values()) {
    const parent = node.m.reply_to ? nodes.get(node.m.reply_to) : undefined;
    if (parent && parent !== node) parent.replies.push(node);
    else roots.push(node);
  }
  for (const node of nodes.values())
    node.replies.sort((a, b) => a.m.time.localeCompare(b.m.time));
  return roots;
}

function byTime(messages: MessageOut[], newestFirst: boolean): MessageOut[] {
  const sorted = [...messages].sort((a, b) => a.time.localeCompare(b.time));
  return newestFirst ? sorted.reverse() : sorted;
}

function AgentLink({ ns, name }: { ns: string; name: string }) {
  return (
    <a
      href={href({ page: "agent", ns, name })}
      onClick={(e) => e.stopPropagation()}
    >
      {name}
    </a>
  );
}

function Details({
  ns,
  agent,
  agents,
}: {
  ns: string;
  agent: OpsIdentityOut;
  agents: OpsIdentityOut[];
}) {
  const now = useNow();
  const children = agents.filter((a) => a.parent === agent.name);
  return (
    <section className="card">
      <div className="row wrap">
        <Badge tone={`role-${agent.role}`}>{agent.role}</Badge>
        <Badge tone={`state-${agent.state}`}>{agent.state}</Badge>
        <span className="muted">
          {agent.kind} &middot; {agent.provider}
          {agent.machine && <> &middot; on {agent.machine}</>}
        </span>
        <span
          className="muted"
          title={agent.last_seen ? fullTime(agent.last_seen) : ""}
        >
          last seen {relativeTime(agent.last_seen, now)}
        </span>
        {agent.tags.map((t) => (
          <span key={t} className="tag">
            {t}
          </span>
        ))}
      </div>
      {agent.description && <p>{agent.description}</p>}
      {agent.parent && (
        <p className="small">
          parent: <AgentLink ns={ns} name={agent.parent} />
        </p>
      )}
      {children.length > 0 && (
        <p className="small">
          children:{" "}
          {children.map((c, i) => (
            <span key={c.name}>
              {i > 0 && ", "}
              <AgentLink ns={ns} name={c.name} />
            </span>
          ))}
        </p>
      )}
    </section>
  );
}

/** Click a row to open the conversation; click a peer name to open that agent. */
function Conversations({
  ns,
  name,
  channels,
}: {
  ns: string;
  name: string;
  channels: ChannelOut[];
}) {
  return (
    <section>
      <h3>Conversations</h3>
      <table className="table">
        <thead>
          <tr>
            <th>with</th>
            <th>type</th>
            <th>messages</th>
          </tr>
        </thead>
        <tbody>
          {channels.map((c) => {
            const peer = otherSide(c.name, name);
            return (
              <tr
                key={c.id}
                className="clickable"
                onClick={() =>
                  navigate({ page: "channel", ns, channelId: c.id })
                }
              >
                <td>
                  {peer && c.kind === "direct" ? (
                    <>
                      <span className="name-icon" title="agent">
                        🤖
                      </span>
                      <AgentLink ns={ns} name={peer} />
                    </>
                  ) : (
                    <>
                      <span className="name-icon" title="room">
                        👥
                      </span>
                      <a
                        href={href({ page: "channel", ns, channelId: c.id })}
                        onClick={(e) => e.stopPropagation()}
                      >
                        #{c.name}
                      </a>
                    </>
                  )}
                </td>
                <td>
                  {c.kind === "direct"
                    ? "DM"
                    : `room, ${c.members.length} members`}
                </td>
                <td>{c.messages}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {channels.length === 0 && <p className="muted">no rooms or DMs</p>}
    </section>
  );
}

const DIRECTIONS: [Direction, string][] = [
  ["from", "from"],
  ["to", "to"],
  ["all", "all"],
];

const DIRECTION_HINTS: Record<Direction, string> = {
  from: "new messages this agent started (no responses)",
  to: "new messages sent to this agent (no responses)",
  all: "every message from or to this agent, responses included",
};

/** Option text for a conversation: the DM peer or the room. */
function conversationLabel(c: ChannelOut, name: string): string {
  const peer = c.kind === "direct" ? otherSide(c.name, name) : null;
  return peer ? `🤖 ${peer}` : `👥 #${c.name}`;
}

function MessageFilters(props: {
  name: string;
  conversations: ChannelOut[];
  conversation: string;
  setConversation: (label: string) => void;
  direction: Direction;
  setDirection: (d: Direction) => void;
  text: string;
  setText: (t: string) => void;
  newestFirst: boolean;
  setNewestFirst: (v: boolean) => void;
  shown: number;
  total: number;
}) {
  return (
    <div className="row wrap filters">
      <select
        value={props.conversation}
        title="conversation"
        onChange={(e) => props.setConversation(e.target.value)}
      >
        <option value="">all</option>
        {props.conversations.map((c) => (
          <option key={c.id} value={channelLabel(c)}>
            {conversationLabel(c, props.name)}
          </option>
        ))}
      </select>
      <span className="toggle">
        {DIRECTIONS.map(([d, label]) => (
          <button
            key={d}
            className={props.direction === d ? "on" : undefined}
            title={DIRECTION_HINTS[d]}
            onClick={() => props.setDirection(d)}
          >
            {label}
          </button>
        ))}
      </span>
      <input
        placeholder="filter by text"
        value={props.text}
        onChange={(e) => props.setText(e.target.value)}
      />
      <button
        className="link"
        onClick={() => props.setNewestFirst(!props.newestFirst)}
      >
        {props.newestFirst ? "newest first ↓" : "oldest first ↑"}
      </button>
      <span className="muted small">
        {props.shown} of {props.total}
      </span>
    </div>
  );
}

function MessageTree(props: {
  nodes: MessageNode[];
  human: HumanResponse | null;
  onOpenThread: (m: MessageOut) => void;
}) {
  return (
    <ul className="messages">
      {props.nodes.map(({ m, replies }) => (
        <MessageBranch key={m.id} m={m} replies={replies} {...props} />
      ))}
    </ul>
  );
}

function MessageBranch(props: {
  m: MessageOut;
  replies: MessageNode[];
  human: HumanResponse | null;
  onOpenThread: (m: MessageOut) => void;
}) {
  const { m, replies, human, onOpenThread } = props;
  return (
    <>
      <MessageItem
        m={m}
        human={human}
        destination={destination(m)}
        marker={isReply(m) ? "reply" : "new"}
        onOpenThread={onOpenThread}
      />
      {replies.length > 0 && (
        <li className="replies">
          <MessageTree
            nodes={replies}
            human={human}
            onOpenThread={onOpenThread}
          />
        </li>
      )}
    </>
  );
}

export function AgentPage({
  ns,
  name,
  agents,
  channels,
  human,
  version,
}: Props) {
  const [threadId, setThreadId] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [newestFirst, setNewestFirst] = useState(true);
  const [direction, setDirection] = useState<Direction>("from");
  const [conversation, setConversation] = useState(""); // a channel label; "" = all

  const mine = agentChannels(channels, name);
  const channelKey = mine.map((c) => c.id).join(",");
  const loaded = useAsync(
    () => loadAll(ns, mine),
    [ns, name, channelKey],
    version,
  );

  const all = loaded.data ?? [];
  const own = new Set(all.filter((m) => m.sender === name).map((m) => m.id));
  const needle = text.trim().toLowerCase();
  const involved = all.filter((m) => involves(m, name, own));
  const filtered = involved.filter(
    (m) =>
      (!conversation || m.channel === conversation) &&
      matchesDirection(m, name, direction, own) &&
      m.text.toLowerCase().includes(needle),
  );
  const messages = byTime(filtered, newestFirst);
  const agent = agents.find((a) => a.name === name);

  return (
    <div className={`channel ${threadId ? "with-thread" : ""}`}>
      <div className="channel-main">
        <header className="channel-head">
          <a
            className="muted small"
            href={href({ page: "ns", ns, section: "agents" })}
          >
            &larr; agents
          </a>
          <h2>{name}</h2>
        </header>
        {agent ? (
          <Details ns={ns} agent={agent} agents={agents} />
        ) : (
          <p className="muted">agent not found</p>
        )}
        <Conversations ns={ns} name={name} channels={mine} />
        <h3>Messages</h3>
        <MessageFilters
          name={name}
          conversations={mine}
          conversation={conversation}
          setConversation={setConversation}
          direction={direction}
          setDirection={setDirection}
          text={text}
          setText={setText}
          newestFirst={newestFirst}
          setNewestFirst={setNewestFirst}
          shown={filtered.length}
          total={involved.length}
        />
        <ErrorBox error={loaded.error} />
        <MessageTree
          nodes={buildTree(messages)}
          human={human}
          onOpenThread={(msg) => setThreadId(msg.thread_id)}
        />
        {loaded.data && messages.length === 0 && (
          <p className="muted">no messages</p>
        )}
      </div>
      {threadId && (
        <ThreadView
          ns={ns}
          threadId={threadId}
          version={version}
          human={human}
          onClose={() => setThreadId(null)}
        />
      )}
    </div>
  );
}
