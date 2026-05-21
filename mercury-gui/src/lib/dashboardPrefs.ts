// dashboardPrefs — localStorage persistence for dashboard UI preferences (#436).
//
// Auto-refresh opt-in lives under two keys:
//   `mercury.dashboard.autoRefresh.enabled`   → "0" | "1"
//   `mercury.dashboard.autoRefresh.intervalMs` → "30000" | "60000" | "300000"
//
// localStorage access is wrapped in try/catch per MDN: SecurityError (sandboxed
// iframe / disabled storage) and QuotaExceededError must both be tolerated.
// In Tauri 2 the storage is browser-emulated and effectively always available,
// but the wrapping keeps the surface honest if anyone runs the bundle in a
// stricter embed.

const KEY_ENABLED = "mercury.dashboard.autoRefresh.enabled";
const KEY_INTERVAL = "mercury.dashboard.autoRefresh.intervalMs";

export const AUTO_REFRESH_INTERVALS_MS = [30_000, 60_000, 300_000] as const;
export type AutoRefreshIntervalMs = (typeof AUTO_REFRESH_INTERVALS_MS)[number];

export const DEFAULT_AUTO_REFRESH_INTERVAL_MS: AutoRefreshIntervalMs = 60_000;

export interface AutoRefreshPrefs {
  enabled: boolean;
  intervalMs: AutoRefreshIntervalMs;
}

const DEFAULT_PREFS: AutoRefreshPrefs = {
  enabled: false,
  intervalMs: DEFAULT_AUTO_REFRESH_INTERVAL_MS,
};

function isValidInterval(n: number): n is AutoRefreshIntervalMs {
  return (AUTO_REFRESH_INTERVALS_MS as readonly number[]).includes(n);
}

export function loadAutoRefreshPrefs(): AutoRefreshPrefs {
  try {
    const enabledRaw = localStorage.getItem(KEY_ENABLED);
    const intervalRaw = localStorage.getItem(KEY_INTERVAL);
    const enabled = enabledRaw === "1";
    const parsed = intervalRaw ? Number.parseInt(intervalRaw, 10) : NaN;
    const intervalMs = isValidInterval(parsed)
      ? parsed
      : DEFAULT_AUTO_REFRESH_INTERVAL_MS;
    return { enabled, intervalMs };
  } catch {
    // SecurityError or storage unavailable — fall back to defaults silently;
    // the toggle still works in-memory for the session.
    return { ...DEFAULT_PREFS };
  }
}

export function saveAutoRefreshPrefs(prefs: AutoRefreshPrefs): void {
  try {
    localStorage.setItem(KEY_ENABLED, prefs.enabled ? "1" : "0");
    localStorage.setItem(KEY_INTERVAL, String(prefs.intervalMs));
  } catch {
    // QuotaExceededError or SecurityError — in-memory state still tracks the
    // user's choice for this session; silent no-op is acceptable since these
    // prefs are convenience-only.
  }
}
