// useGitHubData — fetches GitHub Issue/PR data via Tauri IPC (#416).
// Mirrors useSnapshot pattern: monotonic reqId race guard, silent/manual
// distinction, no auto-refresh (per DoD — rate-limit awareness).
// 60s TTL cache lives in Rust; this hook holds no client-side TTL.

import { useState, useCallback, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { GhSnapshot } from "../lib/ghTypes";

export interface GitHubDataState {
  snapshot: GhSnapshot | null;
  error: string | undefined;
  loading: boolean;
  refresh: (force?: boolean) => void;
}

export function useGitHubData(): GitHubDataState {
  const [snapshot, setSnapshot] = useState<GhSnapshot | null>(null);
  const [error, setError] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState(false);
  // Monotonic request id — late responses from overlapping calls must NOT
  // overwrite newer state (mirrors useSnapshot reqIdRef pattern).
  const reqIdRef = useRef(0);

  const refresh = useCallback(async (force: boolean = false) => {
    const myId = ++reqIdRef.current;
    setLoading(true);
    try {
      const result = await invoke<GhSnapshot>("fetch_gh_dashboard", { force });
      if (myId !== reqIdRef.current) return;
      setSnapshot(result);
      setError(undefined);
    } catch (e) {
      if (myId !== reqIdRef.current) return;
      setError(String(e));
    } finally {
      if (myId === reqIdRef.current) setLoading(false);
    }
  }, []);

  return { snapshot, error, loading, refresh };
}
