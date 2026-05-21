// GitHubDashboard — Issue/PR dashboard view for Phase 6 slice D (#416).
// Filter input with label:/state:/lane:/text prefixes (AND semantics).
// Manual force-refresh button + 60s TTL cache in Rust backend. Opt-in
// auto-refresh toggle (#436) persists via localStorage and skips ticks when
// auth is failing or a fetch is already in flight.

import { useState, useEffect, useRef } from "react";
import { RefreshCw } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { IssueRow } from "./IssueRow";
import { PullRequestRow } from "./PullRequestRow";
import { useGitHubData } from "@/hooks/useGitHubData";
import { parseGhFilter, matchesIssue, matchesPR } from "@/lib/ghFilter";
import { redactHomePaths } from "@/lib/redact";
import { elapsed } from "@/lib/elapsed";
import {
  AUTO_REFRESH_INTERVALS_MS,
  isValidInterval,
  loadAutoRefreshPrefs,
  saveAutoRefreshPrefs,
  type AutoRefreshIntervalMs,
} from "@/lib/dashboardPrefs";

function intervalLabel(ms: AutoRefreshIntervalMs): string {
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`;
  return `${Math.round(ms / 60_000)}m`;
}

function fetchedAtIso(ms: number): string {
  if (!ms) return "";
  return new Date(ms).toISOString();
}

export function GitHubDashboard() {
  const { snapshot, error, authError, loading, refresh } = useGitHubData();
  const [filter, setFilter] = useState("");
  const [initialLoaded, setInitialLoaded] = useState(false);

  // #436 — Opt-in auto-refresh state, hydrated from localStorage on first
  // render. Defaults: enabled=false, intervalMs=60_000. Saved on change.
  const [autoRefreshEnabled, setAutoRefreshEnabled] = useState<boolean>(
    () => loadAutoRefreshPrefs().enabled
  );
  const [autoRefreshIntervalMs, setAutoRefreshIntervalMs] =
    useState<AutoRefreshIntervalMs>(() => loadAutoRefreshPrefs().intervalMs);

  // Refs mirror authError + loading so the auto-refresh interval handler can
  // read the latest values without re-creating the timer whenever they change.
  // Re-creating the timer on every loading toggle would defeat the interval.
  const authErrorRef = useRef(authError);
  const loadingRef = useRef(loading);
  useEffect(() => {
    authErrorRef.current = authError;
  }, [authError]);
  useEffect(() => {
    loadingRef.current = loading;
  }, [loading]);

  useEffect(() => {
    if (!initialLoaded) {
      setInitialLoaded(true);
      refresh(false);
    }
  }, [initialLoaded, refresh]);

  // Persist prefs whenever the user toggles them.
  useEffect(() => {
    saveAutoRefreshPrefs({
      enabled: autoRefreshEnabled,
      intervalMs: autoRefreshIntervalMs,
    });
  }, [autoRefreshEnabled, autoRefreshIntervalMs]);

  // Auto-refresh tick — only when enabled. Skip the tick if a fetch is
  // already in flight (loadingRef) or if the last preflight failed
  // (authErrorRef) so we don't hammer a known-bad auth state. The hook's
  // monotonic reqId guard means an overlapping tick would still be safe,
  // but skipping avoids wasted IPC + log noise.
  useEffect(() => {
    if (!autoRefreshEnabled) return;
    const id = setInterval(() => {
      if (loadingRef.current) return;
      if (authErrorRef.current) return;
      refresh(false);
    }, autoRefreshIntervalMs);
    return () => clearInterval(id);
  }, [autoRefreshEnabled, autoRefreshIntervalMs, refresh]);

  const tokens = parseGhFilter(filter);
  const issues = snapshot?.issues.filter((i) => matchesIssue(i, tokens)) ?? [];
  const prs =
    snapshot?.pullRequests.filter((pr) => matchesPR(pr, tokens)) ?? [];

  const fetchedIso = snapshot ? fetchedAtIso(snapshot.fetchedAt) : "";

  return (
    <div className="flex flex-col gap-4">
      {/* Toolbar: filter + auto-refresh toggle + refresh */}
      <div className="flex gap-2 items-center">
        <Input
          className="flex-1 font-mono text-sm"
          placeholder="Filter: label:P2  state:open  lane:main  or free text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label="Filter issues and pull requests"
        />
        {/* Auto-refresh opt-in (#436) — checkbox + interval selector */}
        <label className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-400 select-none cursor-pointer">
          <input
            type="checkbox"
            className="h-3.5 w-3.5 rounded border-slate-300 dark:border-slate-600"
            checked={autoRefreshEnabled}
            onChange={(e) => setAutoRefreshEnabled(e.target.checked)}
            aria-label="Enable auto-refresh"
          />
          Auto
        </label>
        <select
          className="text-xs px-1.5 py-1 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-300 disabled:opacity-50 disabled:cursor-not-allowed"
          value={autoRefreshIntervalMs}
          onChange={(e) => {
            // Runtime whitelist gate — protects against tampered DOM values
            // (e.g. DevTools-edited <option> emitting 0 or NaN) that would
            // otherwise feed setInterval and cause high-frequency polling.
            // Mirrors loadAutoRefreshPrefs's parse + isValidInterval narrow.
            const parsed = Number.parseInt(e.target.value, 10);
            if (isValidInterval(parsed)) {
              setAutoRefreshIntervalMs(parsed);
            }
          }}
          disabled={!autoRefreshEnabled}
          aria-label="Auto-refresh interval"
          title={
            autoRefreshEnabled
              ? "Auto-refresh interval"
              : "Enable Auto to choose an interval"
          }
        >
          {AUTO_REFRESH_INTERVALS_MS.map((ms) => (
            <option key={ms} value={ms}>
              {intervalLabel(ms)}
            </option>
          ))}
        </select>
        <Button
          variant="outline"
          size="sm"
          onClick={() => refresh(true)}
          disabled={loading}
          aria-label="Force refresh"
          title="Bypass cache and fetch fresh data"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          <span className="ml-1 hidden sm:inline">Refresh</span>
        </Button>
      </div>

      {/* Last-fetched timestamp */}
      {fetchedIso && (
        <p className="text-xs text-slate-400 tabular-nums">
          Last fetched {elapsed(fetchedIso)} ago &middot; {fetchedIso}
        </p>
      )}

      {/* Preflight auth error (#434) — takes precedence over generic fetch
          errors so the actionable "run gh auth login" message is the first
          thing the user sees. */}
      {authError && (
        <div
          role="alert"
          aria-live="polite"
          className="rounded-md border border-amber-200 bg-amber-50 dark:bg-amber-950 dark:border-amber-800 px-4 py-3 text-sm text-amber-800 dark:text-amber-300"
        >
          <strong>GitHub CLI not authenticated.</strong>{" "}
          Run{" "}
          <code className="font-mono px-1 py-0.5 rounded bg-amber-100 dark:bg-amber-900">
            gh auth login
          </code>{" "}
          in your terminal to authenticate, then click <em>Re-check</em>.
          <details className="mt-2 text-xs text-amber-700 dark:text-amber-400">
            <summary className="cursor-pointer select-none">
              Show <code>gh auth status</code> output
            </summary>
            <pre className="mt-1 whitespace-pre-wrap font-mono text-xs">
              {String(redactHomePaths(authError))}
            </pre>
          </details>
          <button
            className="mt-2 underline text-amber-700 dark:text-amber-300 hover:no-underline"
            onClick={() => refresh(true)}
          >
            Re-check authentication
          </button>
        </div>
      )}

      {/* Error state — suppressed when authError is set so the actionable
          preflight toast is not visually crowded by a stale generic error. */}
      {error && !authError && (
        <div className="rounded-md border border-red-200 bg-red-50 dark:bg-red-950 dark:border-red-800 px-4 py-3 text-sm text-red-700 dark:text-red-300">
          <strong>Error fetching GitHub data:</strong>{" "}
          {String(redactHomePaths(error))}
          <br />
          <span className="text-xs text-red-500">
            Ensure <code>gh auth login</code> has been run and the Mercury repo
            is accessible.
          </span>
          <button
            className="ml-4 underline text-red-600 dark:text-red-400 hover:no-underline"
            onClick={() => refresh(true)}
          >
            Retry
          </button>
        </div>
      )}

      {/* Loading state (initial only) — also hidden when authError surfaces
          so the spinner doesn't trail behind the actionable preflight toast. */}
      {loading && !snapshot && !error && !authError && (
        <div className="flex items-center justify-center py-16 text-slate-400 text-sm">
          Loading GitHub data…
        </div>
      )}

      {/* Issues section */}
      {snapshot && (
        <section>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-2">
            Open Issues ({issues.length}
            {tokens.length > 0 &&
              snapshot.issues.length !== issues.length &&
              ` of ${snapshot.issues.length}`}
            )
          </h2>
          <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
            <table className="w-full text-left text-sm" role="table">
              <thead className="bg-slate-100 dark:bg-slate-800 text-xs uppercase tracking-wide text-slate-600 dark:text-slate-400">
                <tr>
                  <th className="px-3 py-2 font-semibold">#</th>
                  <th className="px-3 py-2 font-semibold">Title</th>
                  <th className="px-3 py-2 font-semibold">State</th>
                  <th className="px-3 py-2 font-semibold">Labels</th>
                  <th className="px-3 py-2 font-semibold">Updated</th>
                </tr>
              </thead>
              <tbody className="bg-white dark:bg-slate-900 divide-y divide-slate-100 dark:divide-slate-800">
                {issues.length === 0 ? (
                  <tr>
                    <td
                      colSpan={5}
                      className="px-3 py-8 text-center text-slate-400 text-sm"
                    >
                      {tokens.length > 0
                        ? "No issues match the current filter."
                        : "No open issues."}
                    </td>
                  </tr>
                ) : (
                  issues.map((issue) => (
                    <IssueRow key={issue.number} issue={issue} />
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Pull Requests section */}
      {snapshot && (
        <section>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-2">
            Open Pull Requests ({prs.length}
            {tokens.length > 0 &&
              snapshot.pullRequests.length !== prs.length &&
              ` of ${snapshot.pullRequests.length}`}
            )
          </h2>
          <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
            <table className="w-full text-left text-sm" role="table">
              <thead className="bg-slate-100 dark:bg-slate-800 text-xs uppercase tracking-wide text-slate-600 dark:text-slate-400">
                <tr>
                  <th className="px-3 py-2 font-semibold">#</th>
                  <th className="px-3 py-2 font-semibold">Title</th>
                  <th className="px-3 py-2 font-semibold">Status</th>
                  <th className="px-3 py-2 font-semibold">Labels</th>
                  <th className="px-3 py-2 font-semibold">Updated</th>
                </tr>
              </thead>
              <tbody className="bg-white dark:bg-slate-900 divide-y divide-slate-100 dark:divide-slate-800">
                {prs.length === 0 ? (
                  <tr>
                    <td
                      colSpan={5}
                      className="px-3 py-8 text-center text-slate-400 text-sm"
                    >
                      {tokens.length > 0
                        ? "No pull requests match the current filter."
                        : "No open pull requests."}
                    </td>
                  </tr>
                ) : (
                  prs.map((pr) => <PullRequestRow key={pr.number} pr={pr} />)
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
