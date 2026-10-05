// Typed fetch wrappers for the aichat HTTP API.
// Types mirror packages/aichat-protocol/src/aichat_protocol/models.py.

export type Role = "admin" | "agent" | "human";
export type Kind = "process" | "subagent" | "human";
export type State = "pending" | "active" | "parked" | "done" | "revoked";
export type MessageKind = "question" | "information" | "answer";
export type PostKind = "question" | "information";
export type History = "full" | "since_join";
export type ClosedReason = "max_answers" | "closed" | "expired";

export interface Ref {
  type: "git" | "url";
  repo: string | null;
  branch: string | null;
  commit: string | null;
  path: string | null;
  url: string | null;
}

export interface NamespaceOut {
  id: string;
  title: string;
  created_at: string;
  identities: number;
  rooms: number;
  messages: number;
}

export interface CreateNamespaceRequest {
  title?: string;
  admin_name: string;
  provider: string;
  description?: string;
}

export interface CreateNamespaceResponse {
  ns_id: string;
  title: string;
  admin_name: string;
  join_code: string;
}

export interface OpsIdentityOut {
  name: string;
  role: Role;
  kind: Kind;
  parent: string | null;
  state: State;
  tags: string[];
  provider: string;
  description: string;
  last_seen: string | null;
  depth: number;
  created_at: string;
  session_ref: string | null;
  resume_template: string | null;
}

export interface ChannelOut {
  id: string;
  kind: "room" | "direct";
  name: string;
  members: string[];
  history: History;
  messages: number;
  last_seq: number;
}

export interface MessageOut {
  id: string;
  channel: string;
  seq: number;
  time: string;
  sender: string;
  sender_role: Role;
  kind: MessageKind;
  text: string;
  reply_to: string | null;
  thread_id: string;
  to: string[];
  max_agents_answers: number | null;
  answers_count: number | null;
  closed: boolean | null;
  closed_reason: ClosedReason | null;
  expires_at: string | null;
  refs: Ref[];
  amended_at: string | null;
}

export interface MessagesPage {
  messages: MessageOut[];
  cursor: string | null;
  has_more: boolean;
}

export interface PointerOut {
  reader: string;
  channel: string;
  acked_seq: number;
}

export interface AuditOut {
  ts: string;
  actor: string;
  action: string;
  target: string;
  msg_id: string | null;
  detail: string;
}

export interface EventOut {
  seq: number;
  time: string;
  ns: string | null;
  type: string;
  data: Record<string, unknown>;
}

export interface HumanResponse {
  name: string;
  token: string;
}

export interface Recipient {
  name: string;
  state: State;
  wake: "queued" | "none";
}

export interface PostResponse {
  message: MessageOut;
  recipients: Recipient[];
}

export interface PostMessageRequest {
  text: string;
  kind: PostKind;
  max_agents_answers?: number;
  to?: string[];
}

// --- errors -------------------------------------------------------------------

/** An error from the server's {"error": {"code", "message"}} envelope, or a transport failure. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function toApiError(resp: Response): Promise<ApiError> {
  const body = await resp.text();
  try {
    const parsed = JSON.parse(body) as { error?: { code?: string; message?: string } };
    if (parsed.error?.code) {
      return new ApiError(resp.status, parsed.error.code, parsed.error.message ?? "");
    }
  } catch {
    // Not JSON (for example a proxy error page): fall through to the status text.
  }
  return new ApiError(resp.status, "http_" + resp.status, resp.statusText || "request failed");
}

// --- operator key --------------------------------------------------------------

const OPERATOR_KEY = "aichat.operatorKey";

export function getOperatorKey(): string | null {
  return sessionStorage.getItem(OPERATOR_KEY);
}

export function setOperatorKey(key: string | null): void {
  if (key) sessionStorage.setItem(OPERATOR_KEY, key);
  else sessionStorage.removeItem(OPERATOR_KEY);
}

type UnauthorizedHandler = () => void;
let onUnauthorized: UnauthorizedHandler = () => {};

/** Called when an /ops call returns 401: the key is wrong or was rotated. */
export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  onUnauthorized = handler;
}

// --- transport -----------------------------------------------------------------

async function request<T>(method: string, path: string, headers: Record<string, string>, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: { ...headers } };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  let resp: Response;
  try {
    resp = await fetch(path, init);
  } catch (exc) {
    throw new ApiError(0, "network", exc instanceof Error ? exc.message : "network error");
  }
  if (!resp.ok) throw await toApiError(resp);
  return (await resp.json()) as T;
}

function opsHeaders(): Record<string, string> {
  return { "X-Operator-Key": getOperatorKey() ?? "" };
}

async function ops<T>(method: string, path: string, body?: unknown): Promise<T> {
  try {
    return await request<T>(method, "/ops" + path, opsHeaders(), body);
  } catch (exc) {
    if (exc instanceof ApiError && exc.status === 401) onUnauthorized();
    throw exc;
  }
}

function query(params: Record<string, string | number | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  }
  const s = q.toString();
  return s ? "?" + s : "";
}

const seg = encodeURIComponent;

// --- operator API ----------------------------------------------------------------

export interface MessageQuery {
  channel_id?: string;
  sender?: string;
  kind?: string;
  thread_id?: string;
  before_seq?: number;
  limit?: number;
}

export const opsApi = {
  /** Checks a key without touching the stored one. */
  checkKey: (key: string) => request<NamespaceOut[]>("GET", "/ops/namespaces", { "X-Operator-Key": key }),
  namespaces: () => ops<NamespaceOut[]>("GET", "/namespaces"),
  createNamespace: (req: CreateNamespaceRequest) => ops<CreateNamespaceResponse>("POST", "/namespaces", req),
  deleteNamespace: (ns: string) => ops<{ deleted: string }>("DELETE", `/namespaces/${seg(ns)}`),
  agents: (ns: string) => ops<OpsIdentityOut[]>("GET", `/ns/${seg(ns)}/agents`),
  channels: (ns: string) => ops<ChannelOut[]>("GET", `/ns/${seg(ns)}/channels`),
  messages: (ns: string, q: MessageQuery) => ops<MessagesPage>("GET", `/ns/${seg(ns)}/messages${query({ ...q })}`),
  questions: (ns: string, limit = 100) => ops<MessageOut[]>("GET", `/ns/${seg(ns)}/questions${query({ limit })}`),
  pointers: (ns: string) => ops<PointerOut[]>("GET", `/ns/${seg(ns)}/pointers`),
  audit: (ns: string, limit = 200) => ops<AuditOut[]>("GET", `/ns/${seg(ns)}/audit${query({ limit })}`),
  createHuman: (ns: string, name: string) => ops<HumanResponse>("POST", `/ns/${seg(ns)}/humans`, { name }),
};

// --- human API (Bearer token of a human identity) -----------------------------------

export function humanApi(token: string) {
  const auth = { Authorization: `Bearer ${token}` };
  return {
    postRoom: (room: string, req: PostMessageRequest) =>
      request<PostResponse>("POST", `/rooms/${seg(room)}/messages`, auth, req),
    postDm: (peer: string, req: PostMessageRequest) => request<PostResponse>("POST", `/dm/${seg(peer)}`, auth, req),
    answer: (questionId: string, text: string) =>
      request<PostResponse>("POST", `/questions/${seg(questionId)}/answers`, auth, { text }),
  };
}

// --- helpers ----------------------------------------------------------------------

/** Pointer and message labels: "#room" for rooms, the raw "a|b" name for DMs. */
export function channelLabel(c: ChannelOut): string {
  return c.kind === "room" ? "#" + c.name : c.name;
}

/** Humans must use this name prefix (aichat_protocol.names.HUMAN_PREFIX). */
export const HUMAN_PREFIX_TEXT = "human-";
