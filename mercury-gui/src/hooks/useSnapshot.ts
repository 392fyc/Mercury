// useSnapshot — fetches cross-lane data via Tauri IPC and subscribes to
// mercury:data-changed file-watcher events emitted by the Rust watcher (#414).
// Also ticks every 60s to refresh elapsed-time display without a full data fetch.

import { useEffect, useState, useCallback, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type { JobState, Lane, RosterSnapshot } from "../lib/types";

export interface SnapshotState {
  jobsByLane: Record<string, JobState[]>;
  lanes: Lane[];
  roster: RosterSnapshot | null;
  error: string | undefined;
  loading: boolean;
  elapsedNonce: number; // incremented every 60s to force elapsed re-render
  refresh: () => void;
}

export function useSnapshot(): SnapshotState {
  const [jobsByLane, setJobsByLane] = useState<Record<string, JobState[]>>({});
  const [lanes, setLanes] = useState<Lane[]>([]);
  const [roster, setRoster] = useState<RosterSnapshot | null>(null);
  const [error, setError] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [elapsedNonce, setElapsedNonce] = useState(0);
  // Monotonic request id — late responses from overlapping refresh() calls
  // (initial load + watcher event + manual refresh) must NOT overwrite newer
  // state. Each invocation captures its own id and bails if a later one ran.
  const reqIdRef = useRef(0);

  const refresh = useCallback(async () => {
    const myId = ++reqIdRef.current;
    try {
      const [jbl, ls, rs] = await Promise.all([
        invoke<Record<string, JobState[]>>("read_jobs_by_lane"),
        invoke<Lane[]>("read_lanes"),
        invoke<RosterSnapshot>("read_roster"),
      ]);
      if (myId !== reqIdRef.current) return;
      setJobsByLane(jbl);
      setLanes(ls);
      setRoster(rs);
      setError(undefined);
    } catch (e) {
      if (myId !== reqIdRef.current) return;
      setError(String(e));
    } finally {
      if (myId === reqIdRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let unlisten: UnlistenFn | undefined;

    const safeRefresh = async () => {
      if (!cancelled) await refresh();
    };

    safeRefresh();

    // Subscribe to file-watcher events from Rust watcher (#414).
    // Race-safety: if cleanup runs before `listen()` resolves, immediately
    // unsubscribe the (eventually-resolved) handle instead of leaking the
    // listener across StrictMode dev double-invoke. Catch is non-fatal so
    // manual refresh still works.
    (async () => {
      try {
        const fn = await listen("mercury:data-changed", () => {
          if (!cancelled) safeRefresh();
        });
        if (cancelled) fn();
        else unlisten = fn;
      } catch {
        // Event subscription failure is non-fatal
      }
    })();

    // Re-render every 60s for elapsed freshness even without file-watcher events
    const elapsedTick = setInterval(
      () => setElapsedNonce((n) => n + 1),
      60_000
    );

    return () => {
      cancelled = true;
      if (unlisten) unlisten();
      clearInterval(elapsedTick);
    };
  }, [refresh]);

  return { jobsByLane, lanes, roster, error, loading, elapsedNonce, refresh };
}
