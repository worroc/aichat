// Live state shared by all pages: the operator event stream, a refresh
// counter per namespace, the event log and alert toasts.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { EventOut } from "../api/client";
import { openOpsStream, type StreamStatus } from "../api/sse";

const REFRESH_TYPES = new Set([
  "message",
  "agent_created",
  "agent_joined",
  "agent_revoked",
  "presence",
  "room_created",
  "member_added",
  "member_removed",
  "question_closed",
  "answer_amended",
  "alert",
  "namespace_created",
  "contact_added",
  "contact_removed",
]);

const DEBOUNCE_MS = 400;
const LOG_SIZE = 300;

export interface Alert {
  id: number;
  event: EventOut;
}

interface LiveValue {
  status: StreamStatus;
  statusDetail: string | undefined;
  events: EventOut[];
  alerts: Alert[];
  dismissAlert: (id: number) => void;
  /** Bumps on any refresh-worthy event (used by the namespace list). */
  globalVersion: number;
  /** Bumps on refresh-worthy events of one namespace. */
  versionOf: (ns: string) => number;
}

const LiveContext = createContext<LiveValue | null>(null);

export function useLive(): LiveValue {
  const value = useContext(LiveContext);
  if (!value) throw new Error("useLive outside LiveProvider");
  return value;
}

interface Props {
  operatorKey: string;
  onUnauthorized: () => void;
  children: ReactNode;
}

export function LiveProvider({ operatorKey, onUnauthorized, children }: Props) {
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const [statusDetail, setStatusDetail] = useState<string | undefined>();
  const [events, setEvents] = useState<EventOut[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [versions, setVersions] = useState<{ global: number; byNs: Record<string, number> }>({ global: 0, byNs: {} });
  const pending = useRef<Set<string>>(new Set());
  const timer = useRef<number | undefined>(undefined);
  const openedAt = useRef(Date.now());
  const unauthorized = useRef(onUnauthorized);
  unauthorized.current = onUnauthorized;

  const flush = useCallback(() => {
    timer.current = undefined;
    const touched = pending.current;
    pending.current = new Set();
    setVersions((v) => {
      const byNs = { ...v.byNs };
      for (const ns of touched) byNs[ns] = (byNs[ns] ?? 0) + 1;
      return { global: v.global + 1, byNs };
    });
  }, []);

  const onEvent = useCallback(
    (event: EventOut) => {
      setEvents((old) => [...old.slice(-(LOG_SIZE - 1)), event]);
      // Replayed history also contains old alerts; only toast the new ones.
      if (event.type === "alert" && Date.parse(event.time) >= openedAt.current - 5000) {
        setAlerts((old) => [...old, { id: event.seq, event }]);
      }
      if (!REFRESH_TYPES.has(event.type)) return;
      if (event.ns) pending.current.add(event.ns);
      if (timer.current === undefined) timer.current = window.setTimeout(flush, DEBOUNCE_MS);
    },
    [flush],
  );

  useEffect(() => {
    openedAt.current = Date.now();
    const stop = openOpsStream(operatorKey, {
      onEvent,
      onStatus: (s, detail) => {
        setStatus(s);
        setStatusDetail(detail);
        if (s === "unauthorized") unauthorized.current();
      },
    });
    return () => {
      stop();
      window.clearTimeout(timer.current);
      timer.current = undefined;
    };
  }, [operatorKey, onEvent]);

  const dismissAlert = useCallback((id: number) => setAlerts((old) => old.filter((a) => a.id !== id)), []);
  const value = useMemo<LiveValue>(
    () => ({
      status,
      statusDetail,
      events,
      alerts,
      dismissAlert,
      globalVersion: versions.global,
      versionOf: (ns: string) => versions.byNs[ns] ?? 0,
    }),
    [status, statusDetail, events, alerts, dismissAlert, versions],
  );
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}
