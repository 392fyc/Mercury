// Elapsed time computation from createdAt ISO string to now.
// Returns human-readable string: "12s" | "5m" | "3h" | "2d" | "—" if absent/invalid.

export function elapsed(createdAtIso: string | undefined): string {
  if (!createdAtIso) return "—";
  const ms = Date.now() - new Date(createdAtIso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.floor(h / 24);
  return `${d}d`;
}
