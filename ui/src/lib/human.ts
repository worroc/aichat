// Human identity per namespace, kept in sessionStorage (gone when the tab closes).
import { useCallback, useState } from "react";
import type { HumanResponse } from "../api/client";

const key = (ns: string) => `aichat.human.${ns}`;

function load(ns: string): HumanResponse | null {
  const raw = sessionStorage.getItem(key(ns));
  if (!raw) return null;
  try {
    return JSON.parse(raw) as HumanResponse;
  } catch {
    return null;
  }
}

export function useHuman(ns: string) {
  const [human, setHumanState] = useState<HumanResponse | null>(() => load(ns));
  const [forNs, setForNs] = useState(ns);
  if (forNs !== ns) {
    setForNs(ns);
    setHumanState(load(ns));
  }
  const setHuman = useCallback(
    (value: HumanResponse | null) => {
      if (value) sessionStorage.setItem(key(ns), JSON.stringify(value));
      else sessionStorage.removeItem(key(ns));
      setHumanState(value);
    },
    [ns],
  );
  return { human, setHuman };
}
