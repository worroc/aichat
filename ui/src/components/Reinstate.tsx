// How to bring an agent's session back: machine, dir, session and commands.
import type { OpsIdentityOut } from "../api/client";
import { CopyButton } from "./CopyButton";

/** Quote a value for a POSIX shell when it needs it (like Python's shlex.quote). */
export function shellQuote(value: string): string {
  if (value === "") return "''";
  return /^[\w@%+=:,./-]+$/.test(value) ? value : `'${value.replace(/'/g, `'"'"'`)}'`;
}

/** Split a command line into words like Python's shlex.split (POSIX mode). */
export function shellSplit(line: string): string[] {
  const words: string[] = [];
  let word: string | null = null;
  let quote: "'" | '"' | null = null;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (quote === "'") {
      if (c === "'") quote = null;
      else word += c;
    } else if (quote === '"') {
      if (c === '"') quote = null;
      else if (c === "\\" && i + 1 < line.length && '"\\$`'.includes(line[i + 1])) word += line[++i];
      else word += c;
    } else if (/\s/.test(c)) {
      if (word !== null) words.push(word);
      word = null;
    } else {
      word ??= "";
      if (c === "'" || c === '"') quote = c;
      else if (c === "\\" && i + 1 < line.length) word += line[++i];
      else word += c;
    }
  }
  if (word !== null) words.push(word);
  return words;
}

const PRINT_FLAGS = new Set(["-p", "--print"]);

/**
 * The resume template as a shell line a human can paste, run in the agent's dir.
 * Split like `aichat supervise` does, fill {session} and {name}, quote each word.
 * A human resumes interactively, so {prompt} (and a -p/--print just before it) is dropped.
 */
export function resumeLine(agent: OpsIdentityOut): string | null {
  if (!agent.resume_template) return null;
  const words: string[] = [];
  for (const word of shellSplit(agent.resume_template)) {
    if (word.includes("{prompt}")) {
      if (PRINT_FLAGS.has(words[words.length - 1])) words.pop();
      continue;
    }
    words.push(word.split("{session}").join(agent.session_ref ?? "").split("{name}").join(agent.name));
  }
  const line = words.map(shellQuote).join(" ");
  return agent.cwd ? `cd ${shellQuote(agent.cwd)} && ${line}` : line;
}

function Row({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="reinstate-row">
      <span className="muted">{label}</span>
      {value ? <code>{value}</code> : <span className="muted">unknown</span>}
      {value && <CopyButton text={value} />}
    </div>
  );
}

export function Reinstate({ agent }: { agent: OpsIdentityOut }) {
  const known = agent.machine || agent.cwd || agent.session_ref || agent.command;
  return (
    <section className="card">
      <h3>Reinstate</h3>
      <Row label="host" value={agent.machine} />
      <Row label="dir" value={agent.cwd} />
      <Row label="session" value={agent.session_ref} />
      <Row label="resume" value={resumeLine(agent)} />
      <Row label="started with" value={agent.command} />
      {!known || !agent.session_ref ? (
        <p className="small muted">
          The agent can record this itself:{" "}
          <code>aichat launch --session &lt;id&gt; --cwd . --resume-template '…'</code>
        </p>
      ) : null}
    </section>
  );
}
