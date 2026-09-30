import { useEffect, useState } from "react";

/** Current time, refreshed every `ms` so relative times stay fresh. */
export function useNow(ms = 10000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}
