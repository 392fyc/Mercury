// Types mirroring the Rust models from #414 data layer.
// All Option<T> in Rust → field?: T in TS.
// Schema-tolerant: treat all fields as possibly absent except core identifiers.

export interface InFlight {
  tasks: number;
  queued: number;
  kinds: string[];
}

export interface JobState {
  state: string; // raw token: "done" | "working" | "idle" | "needs_input" | "failed" | "stopped" | <unknown>
  detail: string;
  tempo: string;
  output: unknown | null;
  children: unknown | null;
  linkScanOffset: number;
  template: string; // "bg" | "research" | "dev" | "main" | ...
  respawnFlags: string[];
  intent: string;
  sessionId: string; // UUID
  resumeSessionId: string;
  daemonShort: string; // 8-char hex prefix
  cwd: string; // ~-redacted Rust-side
  createdAt?: string; // ISO 8601, may be absent on malformed file
  updatedAt?: string;
  firstTerminalAt?: string;
  backend: string; // typically "daemon"
  inFlight?: InFlight;
  linkScanPath?: string;
  cliVersion?: string;
  name?: string; // auto-generated display name
  nameSource?: string;
  worktreePath?: string;
  worktreeBranch?: string;
}

export interface RosterSnapshot {
  proto: number;
  supervisorPid: number;
  updatedAt?: string; // ISO 8601
  workers: Record<string, unknown>;
}

export interface Lane {
  name: string;
  worktreePath?: string;
  status?: string;
}
