import { useCallback, useEffect, useRef, useState } from "react";

export interface AsyncState<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  reload: () => void;
}

/**
 * Runs `load` when `deps` change and whenever `version` bumps (live refresh).
 * Keeps the previous data while reloading so the page does not flicker.
 */
export function useAsync<T>(load: () => Promise<T>, deps: unknown[], version = 0): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    let live = true;
    setLoading(true);
    loadRef
      .current()
      .then((value) => {
        if (!live) return;
        setData(value);
        setError(null);
      })
      .catch((exc: unknown) => live && setError(exc))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [...deps, version, tick]);

  // Clear stale data when the identity of what we load changes.
  const depsKey = JSON.stringify(deps);
  const [lastKey, setLastKey] = useState(depsKey);
  if (lastKey !== depsKey) {
    setLastKey(depsKey);
    setData(null);
  }

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}
