// Width of a panel that the user drags from its left edge. Kept in localStorage.
import { useCallback, useEffect, useRef, useState } from "react";

interface Options {
  storageKey: string;
  initial: number;
  min: number;
  /** Largest width as a share of the window width. */
  maxShare: number;
}

function load(key: string, fallback: number): number {
  try {
    const value = Number(localStorage.getItem(key));
    return value > 0 ? value : fallback;
  } catch {
    return fallback;
  }
}

function save(key: string, width: number): void {
  try {
    localStorage.setItem(key, String(Math.round(width)));
  } catch {
    // storage blocked: the width just resets on reload
  }
}

/** Returns the width and a pointer-down handler for the drag handle. */
export function useResizable({ storageKey, initial, min, maxShare }: Options) {
  const [width, setWidth] = useState(() => load(storageKey, initial));
  const latest = useRef(width);
  latest.current = width;

  const clamp = useCallback(
    (value: number) => Math.min(Math.max(value, min), Math.max(min, window.innerWidth * maxShare)),
    [min, maxShare],
  );

  useEffect(() => {
    const onResize = () => setWidth((w) => clamp(w));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [clamp]);

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      const startWidth = latest.current;
      document.body.classList.add("resizing");
      const onMove = (ev: PointerEvent) => setWidth(clamp(startWidth + startX - ev.clientX));
      const onUp = () => {
        document.body.classList.remove("resizing");
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        save(storageKey, latest.current);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [clamp, storageKey],
  );

  return { width, onPointerDown };
}
