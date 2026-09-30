const UNITS: [number, string][] = [
  [60, "s"],
  [60, "m"],
  [24, "h"],
  [7, "d"],
];

/** "12s ago", "5m ago", "3d ago"; "never" for null. */
export function relativeTime(iso: string | null, now: number = Date.now()): string {
  if (!iso) return "never";
  let value = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  for (const [size, unit] of UNITS) {
    if (value < size) return `${value}${unit} ago`;
    value = Math.floor(value / size);
  }
  return new Date(iso).toLocaleDateString();
}

export function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString();
}

export function fullTime(iso: string): string {
  return new Date(iso).toLocaleString();
}
