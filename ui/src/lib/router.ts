// Tiny hash router. Hash URLs keep reloads away from the dev proxy,
// which forwards paths like /rooms and /dm to the API server.
import { useEffect, useState } from "react";

export type Section = "agents" | "questions" | "pointers" | "audit";

export type Route =
  | { page: "namespaces" }
  | { page: "ns"; ns: string; section: Section }
  | { page: "channel"; ns: string; channelId: string }
  | { page: "thread"; ns: string; threadId: string };

const SECTIONS: Section[] = ["agents", "questions", "pointers", "audit"];

export function parseHash(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  if (parts[0] !== "ns" || !parts[1]) return { page: "namespaces" };
  const ns = parts[1];
  if (parts[2] === "c" && parts[3]) return { page: "channel", ns, channelId: parts[3] };
  if (parts[2] === "t" && parts[3]) return { page: "thread", ns, threadId: parts[3] };
  const section = SECTIONS.find((s) => s === parts[2]) ?? "agents";
  return { page: "ns", ns, section };
}

export function href(route: Route): string {
  const e = encodeURIComponent;
  switch (route.page) {
    case "namespaces":
      return "#/";
    case "ns":
      return `#/ns/${e(route.ns)}/${route.section}`;
    case "channel":
      return `#/ns/${e(route.ns)}/c/${e(route.channelId)}`;
    case "thread":
      return `#/ns/${e(route.ns)}/t/${e(route.threadId)}`;
  }
}

export function navigate(route: Route): void {
  window.location.hash = href(route);
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
