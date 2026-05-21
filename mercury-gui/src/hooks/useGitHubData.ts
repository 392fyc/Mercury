// useGitHubData — fetches GitHub Issue/PR data via Tauri IPC (#416).
// Mirrors useSnapshot pattern: monotonic reqId race guard.
// No auto-refresh and no silent/manual distinction (per DoD — rate-limit
// awareness); refresh() is always user-initiated.
// 60s TTL cache lives in Rust; this hook holds no client-side TTL.
//
// #434: Every `refresh()` runs `gh auth status` as a preflight BEFORE the
// issue/PR fetch. A failed preflight surfaces `authError` (distinct from
// `error`) and short-circuits the fetch so the toast can offer a clear
// "run `gh auth login`" remediation. Preflight runs on every refresh (not
// just once) so mid-session token revocation surfaces the actionable toast
// instead of the deeper `gh issue list` stderr passthrough. The preflight
// is a single local subprocess (~50ms) and refresh is user-initiated, so
// the per-click cost is negligible.

import { useState, useCallback, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { GhSnapshot, GhAuthStatus } from "../lib/ghTypes";

export interface GitHubDataState {
  snapshot: GhSnapshot | null;
  error: string | undefined;
  authError: string | undefined;
  loading: boolean;
  refresh: (force?: boolean) => void;
}

export function useGitHubData(): GitHubDataState {
  const [snapshot, setSnapshot] = useState<GhSnapshot | null>(null);
  const [error, setError] = useState<string | undefined>(undefined);
  const [authError, setAuthError] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState(false);
  // Monotonic request id — late responses from overlapping calls must NOT
  // overwrite newer state (mirrors useSnapshot reqIdRef pattern).
  const reqIdRef = useRef(0);

  const refresh = useCallback(async (force: boolean = false) => {
    const myId = ++reqIdRef.current;
    setLoading(true);
    try {
      const auth = await invoke<GhAuthStatus>("check_gh_auth");
      if (myId !== reqIdRef.current) return;
      if (!auth.authenticated) {
        setAuthError(auth.message || "gh CLI is not authenticated");
        setError(undefined);
        return;
      }
      setAuthError(undefined);
      const result = await invoke<GhSnapshot>("fetch_gh_dashboard", { force });
      if (myId !== reqIdRef.current) return;
      setSnapshot(result);
      setError(undefined);
    } catch (e) {
      if (myId !== reqIdRef.current) return;
      // Clear authError on the exception path too: if a previous run set it
      // and the current preflight throws (IPC/capability/spawn failure), the
      // dashboard would otherwise keep showing the stale "run gh auth login"
      // toast (which suppresses the generic error panel) — masking the real
      // cause. The exception is, by definition, a different failure class
      // than the previous "exit 1 = not authenticated" verdict.
      setAuthError(undefined);
      setError(String(e));
    } finally {
      if (myId === reqIdRef.current) setLoading(false);
    }
  }, []);

  return { snapshot, error, authError, loading, refresh };
}
