import { opsApi, type ChannelOut, type OpsIdentityOut } from "../api/client";
import { EventLog } from "../components/EventLog";
import { ErrorBox } from "../components/ErrorBox";
import { HumanPanel } from "../components/HumanPanel";
import { ThreadView } from "../components/ThreadView";
import { useHuman } from "../lib/human";
import { useLive } from "../lib/live";
import { href, type Route, type Section } from "../lib/router";
import { useAsync } from "../lib/useAsync";
import { AgentPage } from "./AgentPage";
import { ChannelPage } from "./ChannelPage";
import { AgentsSection, AuditSection, PointersSection, QuestionsSection } from "./SectionPages";

type NsRoute = Exclude<Route, { page: "namespaces" }>;

const SECTIONS: [Section, string][] = [
  ["agents", "Agents"],
  ["questions", "Questions"],
  ["pointers", "Read status"],
  ["audit", "Audit"],
];

/** Humans first in "Direct": each opens the page with everything the human wrote or got. */
function HumanLinks({ ns, humans, active }: { ns: string; humans: OpsIdentityOut[]; active?: string }) {
  return (
    <>
      {humans.map((h) => (
        <li key={h.name} className={h.name === active ? "active" : undefined}>
          <a href={href({ page: "agent", ns, name: h.name })} title="all messages of this human">
            <span className="clip">🧑 {h.name}</span>
          </a>
        </li>
      ))}
    </>
  );
}

function ChannelLinks(props: {
  ns: string;
  title: string;
  channels: ChannelOut[];
  active?: string;
  humans?: OpsIdentityOut[];
  activeHuman?: string;
}) {
  const { ns, title, channels, active, humans = [] } = props;
  return (
    <section>
      <h4>{title}</h4>
      <ul className="nav">
        <HumanLinks ns={ns} humans={humans} active={props.activeHuman} />
        {channels.map((c) => (
          <li key={c.id} className={c.id === active ? "active" : undefined}>
            <a href={href({ page: "channel", ns, channelId: c.id })}>
              <span className="clip">{c.kind === "room" ? "#" + c.name : c.name.replace("|", " ↔ ")}</span>
              <span className="count">{c.last_seq}</span>
            </a>
          </li>
        ))}
        {channels.length === 0 && humans.length === 0 && <li className="muted small">none</li>}
      </ul>
    </section>
  );
}

function Sidebar(props: {
  route: NsRoute;
  channels: ChannelOut[];
  agents: OpsIdentityOut[];
  humanPanel: React.ReactNode;
}) {
  const { route, channels } = props;
  const active = route.page === "channel" ? route.channelId : undefined;
  const activeHuman = route.page === "agent" ? route.name : undefined;
  const humans = props.agents.filter((a) => a.role === "human" && a.state !== "revoked");
  return (
    <nav className="sidebar">
      <a className="muted small" href={href({ page: "namespaces" })}>
        &larr; namespaces
      </a>
      <code className="ns-id" title={route.ns}>
        {route.ns}
      </code>
      <ul className="nav">
        {SECTIONS.map(([section, label]) => (
          <li key={section} className={route.page === "ns" && route.section === section ? "active" : undefined}>
            <a href={href({ page: "ns", ns: route.ns, section })}>{label}</a>
          </li>
        ))}
      </ul>
      <ChannelLinks ns={route.ns} title="Rooms" channels={channels.filter((c) => c.kind === "room")} active={active} />
      <ChannelLinks
        ns={route.ns}
        title="Direct"
        channels={channels.filter((c) => c.kind === "direct")}
        active={active}
        humans={humans}
        activeHuman={activeHuman}
      />
      {props.humanPanel}
    </nav>
  );
}

interface LayoutProps {
  route: NsRoute;
  humanName: string;
  onChangeName: () => void;
}

export function NamespaceLayout({ route, humanName, onChangeName }: LayoutProps) {
  const ns = route.ns;
  const version = useLive().versionOf(ns);
  const { human, error: humanError } = useHuman(ns, humanName);
  const agents = useAsync(() => opsApi.agents(ns), [ns], version);
  const channels = useAsync(() => opsApi.channels(ns), [ns], version);
  const channelList = channels.data ?? [];

  const content = () => {
    if (route.page === "channel") {
      return (
        <ChannelPage
          key={route.channelId}
          ns={ns}
          channelId={route.channelId}
          channel={channelList.find((c) => c.id === route.channelId)}
          agents={agents.data ?? []}
          human={human}
          version={version}
        />
      );
    }
    if (route.page === "agent") {
      return (
        <AgentPage
          key={route.name}
          ns={ns}
          name={route.name}
          agents={agents.data ?? []}
          channels={channelList}
          human={human}
          version={version}
        />
      );
    }
    if (route.page === "thread") {
      return <ThreadView ns={ns} threadId={route.threadId} version={version} human={human} />;
    }
    switch (route.section) {
      case "agents":
        return <AgentsSection ns={ns} agents={agents.data} error={agents.error} />;
      case "questions":
        return <QuestionsSection ns={ns} version={version} />;
      case "pointers":
        return <PointersSection ns={ns} version={version} channels={channels.data} />;
      case "audit":
        return <AuditSection ns={ns} version={version} />;
    }
  };

  return (
    <div className="ns-layout">
      <Sidebar
        route={route}
        channels={channelList}
        agents={agents.data ?? []}
        humanPanel={<HumanPanel name={humanName} human={human} error={humanError} onChangeName={onChangeName} />}
      />
      <main className="content">
        <ErrorBox error={channels.error} />
        {content()}
      </main>
      <EventLog ns={ns} />
    </div>
  );
}
