// Filter primitive parser and matcher for the cross-lane snapshot view.
//
// Supported prefixes (space-separated, AND-combined):
//   a:<template>  — template exact OR `<value>-` prefix match (case-insensitive).
//                   Future templates like `dev-rust` match `a:dev` but `dev1` does NOT.
//   s:<state>     — raw state token EXACT match (case-insensitive). `s:done`, `s:working`.
//   l:<lane>      — lane name SUBSTRING match (case-insensitive). `l:main` matches
//                   `main`, `side-multi-lane`, etc. Use exact name for narrower scope.
//   free text     — substring match on intent + name + detail (case-insensitive).
//
// Empty filter → show all.

import type { JobState } from "./types";

export interface FilterToken {
  kind: "agent" | "state" | "lane" | "text";
  value: string;
}

export function parseFilter(raw: string): FilterToken[] {
  if (!raw.trim()) return [];
  const tokens: FilterToken[] = [];
  for (const tok of raw.trim().split(/\s+/)) {
    const lower = tok.toLowerCase();
    // Empty-value prefix tokens (e.g. bare "a:", "s:", "l:") are silently
    // dropped. Otherwise their match semantics diverge across kinds: `a:`
    // would reject all rows, `l:` would match every lane, `s:` only the
    // empty-state rows. Skipping keeps user feedback consistent.
    if (lower.startsWith("a:")) {
      const value = lower.slice(2);
      if (value) tokens.push({ kind: "agent", value });
      continue;
    }
    if (lower.startsWith("s:")) {
      const value = lower.slice(2);
      if (value) tokens.push({ kind: "state", value });
      continue;
    }
    if (lower.startsWith("l:")) {
      const value = lower.slice(2);
      if (value) tokens.push({ kind: "lane", value });
      continue;
    }
    tokens.push({ kind: "text", value: lower });
  }
  return tokens;
}

export function matchesJob(
  job: JobState,
  laneName: string,
  tokens: FilterToken[]
): boolean {
  if (tokens.length === 0) return true;
  // AND across all tokens
  return tokens.every((token) => {
    switch (token.kind) {
      case "agent": {
        const tmpl = (job.template ?? "").toLowerCase();
        return tmpl === token.value || tmpl.startsWith(token.value + "-");
      }
      case "state": {
        return (job.state ?? "").toLowerCase() === token.value;
      }
      case "lane": {
        return laneName.toLowerCase().includes(token.value);
      }
      case "text": {
        const haystack = [
          job.intent ?? "",
          job.name ?? "",
          job.detail ?? "",
        ]
          .join(" ")
          .toLowerCase();
        return haystack.includes(token.value);
      }
    }
  });
}

// Returns true if ANY job in the lane matches the filter tokens (for lane-level filtering).
// The lane itself is also checked against l: tokens.
export function laneMatchesFilter(
  laneName: string,
  jobs: JobState[],
  tokens: FilterToken[]
): boolean {
  if (tokens.length === 0) return true;

  // If ALL tokens are lane-only type, just match on lane name
  const hasLaneToken = tokens.some((t) => t.kind === "lane");
  const hasNonLane = tokens.some((t) => t.kind !== "lane");

  if (hasLaneToken && !hasNonLane) {
    return tokens
      .filter((t) => t.kind === "lane")
      .every((t) => laneName.toLowerCase().includes(t.value));
  }

  // Otherwise: lane must have at least one job matching all job-relevant tokens
  const jobTokens = tokens.filter((t) => t.kind !== "lane");
  const laneTokens = tokens.filter((t) => t.kind === "lane");

  const laneOk =
    laneTokens.length === 0 ||
    laneTokens.every((t) => laneName.toLowerCase().includes(t.value));

  if (!laneOk) return false;
  if (jobs.length === 0 && jobTokens.length > 0) return false;

  return jobs.some((job) => matchesJob(job, laneName, jobTokens));
}
