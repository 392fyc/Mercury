// Filter parser + matcher for the GitHub Issue/PR dashboard (#416).
//
// Supported prefixes (space-separated, AND-combined):
//   label:<name>  — substring match against any label name (case-insensitive).
//                   `label:P2` matches "P2", "enhancement-P2", etc.
//   state:<value> — exact match on item.state (open/closed), OR `draft` for
//                   PRs where isDraft===true.
//   lane:<value>  — substring match against labels that start with "lane:".
//                   `lane:main` matches a label named "lane:main".
//                   `lane:` (empty value) is dropped (consistent with filter.ts).
//   free text     — substring match on title + number string + author login.
//
// Empty filter → show all (returns empty token array).
// AND semantics: item must satisfy every token.

import type { GhIssue, GhPullRequest } from "./ghTypes";

export interface GhFilterToken {
  kind: "label" | "state" | "lane" | "text";
  value: string;
}

export function parseGhFilter(raw: string): GhFilterToken[] {
  if (!raw.trim()) return [];
  const tokens: GhFilterToken[] = [];
  for (const tok of raw.trim().split(/\s+/)) {
    const lower = tok.toLowerCase();
    // Empty-value prefix tokens are silently dropped (mirrors filter.ts discipline).
    if (lower.startsWith("label:")) {
      const value = lower.slice(6);
      if (value) tokens.push({ kind: "label", value });
      continue;
    }
    if (lower.startsWith("state:")) {
      const value = lower.slice(6);
      if (value) tokens.push({ kind: "state", value });
      continue;
    }
    if (lower.startsWith("lane:")) {
      const value = lower.slice(5);
      if (value) tokens.push({ kind: "lane", value });
      continue;
    }
    tokens.push({ kind: "text", value: lower });
  }
  return tokens;
}

export function matchesIssue(
  issue: GhIssue,
  tokens: GhFilterToken[]
): boolean {
  if (tokens.length === 0) return true;
  return tokens.every((token) => {
    switch (token.kind) {
      case "label": {
        return issue.labels.some((l) =>
          l.name.toLowerCase().includes(token.value)
        );
      }
      case "state": {
        return issue.state.toLowerCase() === token.value;
      }
      case "lane": {
        return issue.labels
          .filter((l) => l.name.toLowerCase().startsWith("lane:"))
          .some((l) => l.name.toLowerCase().includes(token.value));
      }
      case "text": {
        const haystack = [
          issue.title,
          String(issue.number),
          ...issue.assignees.map((a) => a.login),
        ]
          .join(" ")
          .toLowerCase();
        return haystack.includes(token.value);
      }
    }
  });
}

export function matchesPR(
  pr: GhPullRequest,
  tokens: GhFilterToken[]
): boolean {
  if (tokens.length === 0) return true;
  return tokens.every((token) => {
    switch (token.kind) {
      case "label": {
        return pr.labels.some((l) =>
          l.name.toLowerCase().includes(token.value)
        );
      }
      case "state": {
        // `state:draft` matches isDraft PRs regardless of the state field
        if (token.value === "draft") return pr.isDraft;
        return pr.state.toLowerCase() === token.value;
      }
      case "lane": {
        return pr.labels
          .filter((l) => l.name.toLowerCase().startsWith("lane:"))
          .some((l) => l.name.toLowerCase().includes(token.value));
      }
      case "text": {
        const haystack = [
          pr.title,
          String(pr.number),
          pr.headRefName,
          ...pr.assignees.map((a) => a.login),
        ]
          .join(" ")
          .toLowerCase();
        return haystack.includes(token.value);
      }
    }
  });
}
