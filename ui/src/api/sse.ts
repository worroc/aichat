// Operator event stream: GET /ops/stream as Server-Sent Events.
// EventSource cannot send the X-Operator-Key header, so this reads the body
// with fetch() and parses the frames by hand.

import type { EventOut } from "./client";

export type StreamStatus = "connecting" | "open" | "retrying" | "unauthorized";

export interface StreamHandlers {
  onEvent: (event: EventOut) => void;
  onStatus: (status: StreamStatus, detail?: string) => void;
}

interface Frame {
  id: string | null;
  event: string;
  data: string;
}

const RETRY_MS = [1000, 2000, 5000, 10000];

/** Splits a text buffer into complete frames; returns them and the unfinished tail. */
function takeFrames(buffer: string): { frames: Frame[]; rest: string } {
  const normalized = buffer.replace(/\r\n?/g, "\n");
  const blocks = normalized.split("\n\n");
  const rest = blocks.pop() ?? "";
  return { frames: blocks.map(parseBlock).filter((f): f is Frame => f !== null), rest };
}

function parseBlock(block: string): Frame | null {
  const frame: Frame = { id: null, event: "message", data: "" };
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (!line || line.startsWith(":")) continue; // comment: ": connected", ": ping"
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "id") frame.id = value;
    else if (field === "event") frame.event = value;
    else if (field === "data") data.push(value);
  }
  if (data.length === 0) return null;
  frame.data = data.join("\n");
  return frame;
}

function toEvent(frame: Frame): EventOut | null {
  try {
    return JSON.parse(frame.data) as EventOut;
  } catch {
    return null;
  }
}

/**
 * Connects to /ops/stream and keeps reconnecting with ?after=<last id>.
 * Returns a function that stops the stream.
 */
export function openOpsStream(key: string, handlers: StreamHandlers, after = 0): () => void {
  const controller = new AbortController();
  let lastId = after;
  let attempt = 0;
  let lastError: string | undefined;

  const handleFrame = (frame: Frame): boolean => {
    if (frame.event === "error") {
      // The server sends a bare error code here, not JSON.
      lastError = frame.data;
      return false;
    }
    const event = toEvent(frame);
    if (!event) return true;
    if (frame.id) lastId = Number(frame.id) || lastId;
    handlers.onEvent(event);
    return true;
  };

  const readBody = async (body: ReadableStream<Uint8Array>) => {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return;
      buffer += decoder.decode(value, { stream: true });
      const { frames, rest } = takeFrames(buffer);
      buffer = rest;
      for (const frame of frames) {
        if (!handleFrame(frame)) return;
      }
    }
  };

  const connectOnce = async (): Promise<"retry" | "stop"> => {
    handlers.onStatus(attempt === 0 ? "connecting" : "retrying");
    const resp = await fetch(`/ops/stream?after=${lastId}`, {
      headers: { "X-Operator-Key": key, Accept: "text/event-stream" },
      signal: controller.signal,
    });
    if (resp.status === 401) {
      handlers.onStatus("unauthorized");
      return "stop";
    }
    if (!resp.ok || !resp.body) return "retry";
    handlers.onStatus("open");
    attempt = 0;
    lastError = undefined;
    await readBody(resp.body);
    return "retry";
  };

  const loop = async () => {
    while (!controller.signal.aborted) {
      let outcome: "retry" | "stop" = "retry";
      try {
        outcome = await connectOnce();
      } catch {
        if (controller.signal.aborted) return;
      }
      if (outcome === "stop") return;
      const delay = RETRY_MS[Math.min(attempt, RETRY_MS.length - 1)];
      attempt += 1;
      handlers.onStatus("retrying", lastError);
      await new Promise((r) => setTimeout(r, delay));
    }
  };

  void loop();
  return () => controller.abort();
}
