// GitHubDashboard — Issue/PR dashboard view for Phase 6 slice D (#416).
// Filter input with label:/state:/lane:/text prefixes (AND semantics).
// No auto-refresh; manual force-refresh button + 60s TTL cache in Rust backend.

import { useState, useEffect } from "react";
import { RefreshCw } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { IssueRow } from "./IssueRow";
import { PullRequestRow } from "./PullRequestRow";
import { useGitHubData } from "@/hooks/useGitHubData";
import { parseGhFilter, matchesIssue, matchesPR } from "@/lib/ghFilter";
import { redactHomePaths } from "@/lib/redact";
import { elapsed } from "@/lib/elapsed";

function fetchedAtIso(ms: number): string {
  if (!ms) return "";
  return new Date(ms).toISOString();
}

export function GitHubDashboard() {
  const { snapshot, error, loading, refresh } = useGitHubData();
  const [filter, setFilter] = useState("");
  // Load data on first mount (no auto-refresh per DoD)
  const [initialLoaded, setInitialLoaded] = useState(false);

  useEffect(() => {
    if (!initialLoaded) {
      setInitialLoaded(true);
      refresh(false);
    }
  }, [initialLoaded, refresh]);

  const tokens = parseGhFilter(filter);
  const issues = snapshot?.issues.filter((i) => matchesIssue(i, tokens)) ?? [];
  const prs =
    snapshot?.pull_requests.filter((pr) => matchesPR(pr, tokens)) ?? [];

  const fetchedIso = snapshot ? fetchedAtIso(snapshot.fetchedAt) : "";

  return (
    <div className="flex flex-col gap-4">
      {/* Toolbar: filter + refresh */}
      <div className="flex gap-2 items-center">
        <Input
          className="flex-1 font-mono text-sm"
          placeholder="Filter: label:P2  state:open  lane:main  or free text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label="Filter issues and pull requests"
        />
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

      {/* Error state */}
      {error && (
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

      {/* Loading state (initial only) */}
      {loading && !snapshot && !error && (
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
              snapshot.pull_requests.length !== prs.length &&
              ` of ${snapshot.pull_requests.length}`}
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
