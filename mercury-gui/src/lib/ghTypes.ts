// TS mirror of Rust gh_dashboard models (#416).
// Schema-tolerant: optional fields use `?`, null-aware where gh CLI may omit.

export interface GhLabel {
  name: string;
  color?: string | null;
}

export interface GhUser {
  login: string;
}

export interface GhIssue {
  number: number;
  title: string;
  state: string;
  labels: GhLabel[];
  assignees: GhUser[];
  createdAt: string;
  updatedAt: string;
  url: string;
  milestone?: unknown | null;
}

export interface GhPullRequest {
  number: number;
  title: string;
  state: string;
  labels: GhLabel[];
  assignees: GhUser[];
  createdAt: string;
  updatedAt: string;
  url: string;
  headRefName: string;
  baseRefName: string;
  isDraft: boolean;
  reviewDecision?: string | null;
}

export interface GhSnapshot {
  issues: GhIssue[];
  pullRequests: GhPullRequest[];
  // Unix epoch milliseconds — consistent with RosterSnapshot.updatedAt
  fetchedAt: number;
}
