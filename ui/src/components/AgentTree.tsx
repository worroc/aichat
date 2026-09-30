import type { OpsIdentityOut } from "../api/client";
import { relativeTime } from "../lib/time";
import { Badge } from "./Badge";

interface Node {
  agent: OpsIdentityOut;
  level: number;
}

/** Orders agents depth-first by `parent` so children follow their parent. */
function flatten(agents: OpsIdentityOut[]): Node[] {
  const names = new Set(agents.map((a) => a.name));
  const children = new Map<string, OpsIdentityOut[]>();
  const roots: OpsIdentityOut[] = [];
  for (const a of agents) {
    if (a.parent && names.has(a.parent)) {
      const list = children.get(a.parent) ?? [];
      list.push(a);
      children.set(a.parent, list);
    } else {
      roots.push(a);
    }
  }
  const out: Node[] = [];
  const seen = new Set<string>();
  const walk = (a: OpsIdentityOut, level: number) => {
    if (seen.has(a.name)) return;
    seen.add(a.name);
    out.push({ agent: a, level });
    for (const c of children.get(a.name) ?? []) walk(c, level + 1);
  };
  roots.forEach((r) => walk(r, 0));
  agents.forEach((a) => walk(a, 0)); // anything left in a cycle
  return out;
}

export function AgentTree({ agents, now }: { agents: OpsIdentityOut[]; now: number }) {
  return (
    <table className="table">
      <thead>
        <tr>
          <th>name</th>
          <th>role</th>
          <th>kind</th>
          <th>provider</th>
          <th>state</th>
          <th>last seen</th>
          <th>tags</th>
        </tr>
      </thead>
      <tbody>
        {flatten(agents).map(({ agent: a, level }) => (
          <tr key={a.name} className={a.state === "revoked" ? "dim" : undefined}>
            <td style={{ paddingLeft: `${0.5 + level * 1.4}rem` }} title={a.description}>
              {level > 0 && <span className="muted tree-mark">└ </span>}
              <strong>{a.name}</strong>
            </td>
            <td>
              <Badge tone={`role-${a.role}`}>{a.role}</Badge>
            </td>
            <td>{a.kind}</td>
            <td>{a.provider}</td>
            <td>
              <Badge tone={`state-${a.state}`}>{a.state}</Badge>
            </td>
            <td title={a.last_seen ?? ""}>{relativeTime(a.last_seen, now)}</td>
            <td>
              {a.tags.map((t) => (
                <span key={t} className="tag">
                  {t}
                </span>
              ))}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
