// The human the UI acts as.
// - The name is asked once at login and kept in localStorage (it is not a secret).
// - The token is per namespace, made on demand and kept in sessionStorage.
import { useEffect, useState } from "react";
import { opsApi, type HumanResponse } from "../api/client";

const NAME_KEY = "aichat.humanName";
const tokenKey = (ns: string) => `aichat.human.${ns}`;

export function getHumanName(): string | null {
  try {
    return localStorage.getItem(NAME_KEY);
  } catch {
    return null;
  }
}

export function setHumanName(name: string | null): void {
  try {
    if (name) localStorage.setItem(NAME_KEY, name);
    else localStorage.removeItem(NAME_KEY);
  } catch {
    // Storage blocked: the name lives only in memory for this page.
  }
}

/** The cached token for `ns`, only when it was made for `name`. */
function load(ns: string, name: string): HumanResponse | null {
  const raw = sessionStorage.getItem(tokenKey(ns));
  if (!raw) return null;
  try {
    const human = JSON.parse(raw) as HumanResponse;
    return human.name === name ? human : null;
  } catch {
    return null;
  }
}

function save(ns: string, human: HumanResponse): void {
  sessionStorage.setItem(tokenKey(ns), JSON.stringify(human));
}

interface Result {
  ns: string;
  name: string;
  human: HumanResponse | null;
  error: unknown;
}

/** Acts as `name` in `ns`: uses the cached token or asks the hub for one. */
export function useHuman(ns: string, name: string): { human: HumanResponse | null; error: unknown } {
  const [result, setResult] = useState<Result>(() => ({ ns, name, human: load(ns, name), error: null }));

  useEffect(() => {
    const cached = load(ns, name);
    setResult({ ns, name, human: cached, error: null });
    if (cached) return;
    let live = true;
    opsApi.createHuman(ns, name).then(
      (human) => {
        save(ns, human);
        if (live) setResult({ ns, name, human, error: null });
      },
      (error: unknown) => {
        if (live) setResult({ ns, name, human: null, error });
      },
    );
    return () => {
      live = false;
    };
  }, [ns, name]);

  const current = result.ns === ns && result.name === name;
  return { human: current ? result.human : null, error: current ? result.error : null };
}
