import type { MessageOut } from "../api/client";
import { fullTime } from "./time";

/**
 * Whether `m` matches the search box.
 * - "#name" matches messages that carry the tag "name" (exact, any case).
 * - Any other text matches the message text, one of its tags, or its time:
 *   the shown local date and time ("11/5/2026, 6:05:43 PM") or 24-hour ("18:05:43").
 */
export function matchesSearch(m: MessageOut, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const tags = (m.metadata?.tags ?? []).map((t) => t.toLowerCase());
  if (q.startsWith("#") && q.length > 1) return tags.includes(q.slice(1));
  return m.text.toLowerCase().includes(q) || tags.some((t) => t.includes(q)) || timeText(m.time).includes(q);
}

/** The message time as the user may type it: the shown local form and a 24-hour clock. */
function timeText(iso: string): string {
  const d = new Date(iso);
  const clock24 = d.toLocaleTimeString("en-GB", { hour12: false });
  return `${fullTime(iso)} ${clock24}`.toLowerCase();
}
