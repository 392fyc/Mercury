use super::{redact_home, redact_home_in_json};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Per-session state from `<claude_config_dir>/jobs/<id>/state.json`.
///
/// Schema-tolerant per S120 ADR — every field that may be absent on older
/// CLI versions or non-terminal states is `Option<T>` + `#[serde(default)]`.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
#[serde(default)]
pub struct JobState {
    pub state: String,
    pub detail: String,
    pub tempo: String,
    pub output: Option<serde_json::Value>,
    pub children: Option<serde_json::Value>,
    pub link_scan_offset: i64,
    pub template: String,
    pub respawn_flags: Vec<String>,
    pub intent: String,
    pub session_id: String,
    pub resume_session_id: String,
    pub daemon_short: String,
    pub cwd: String,
    pub created_at: Option<DateTime<Utc>>,
    pub updated_at: Option<DateTime<Utc>>,
    pub first_terminal_at: Option<DateTime<Utc>>,
    pub backend: String,

    pub in_flight: Option<InFlight>,
    pub link_scan_path: Option<String>,
    pub cli_version: Option<String>,
    pub name: Option<String>,
    pub name_source: Option<String>,

    pub worktree_path: Option<String>,
    pub worktree_branch: Option<String>,
}

impl JobState {
    /// Strip the home directory prefix from path-bearing fields before
    /// surfacing to the frontend. Mirrors the same scrubbing that `DataError`
    /// applies to error messages so the GUI never sees `C:\Users\<name>\...`
    /// literals. Idempotent.
    ///
    /// Coverage:
    /// - Typed top-level fields: `cwd`, `link_scan_path`, `worktree_path`
    /// - Untyped JSON blobs (recursive walk): `output`, `children` — see
    ///   `redact_home_in_json` for the walker contract. Closes the S125
    ///   carry-forward gap where LLM-emitted home paths in nested JSON
    ///   strings could leak past the typed-field scrub.
    pub fn sanitize_paths(&mut self) {
        self.cwd = redact_home(&self.cwd);
        if let Some(s) = self.link_scan_path.as_deref() {
            self.link_scan_path = Some(redact_home(s));
        }
        if let Some(s) = self.worktree_path.as_deref() {
            self.worktree_path = Some(redact_home(s));
        }
        if let Some(v) = self.output.as_mut() {
            redact_home_in_json(v);
        }
        if let Some(v) = self.children.as_mut() {
            redact_home_in_json(v);
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
#[serde(default)]
pub struct InFlight {
    pub tasks: i64,
    pub queued: i64,
    pub kinds: Vec<String>,
}

/// Supervisor heartbeat + active-worker registry from `<claude_config_dir>/daemon/roster.json`.
///
/// `updated_at` is unix milliseconds in source JSON; dispatched via `chrono::serde::ts_milliseconds`.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
#[serde(default)]
pub struct RosterSnapshot {
    pub proto: i64,
    pub supervisor_pid: i64,
    #[serde(with = "chrono::serde::ts_milliseconds_option")]
    pub updated_at: Option<DateTime<Utc>>,
    pub workers: HashMap<String, serde_json::Value>,
}

/// Mercury lane entry parsed from LANES.md.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Lane {
    pub name: String,
    pub worktree_path: Option<String>,
    pub status: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Sample from S120 ADR — sessionId `40726463`, cliVersion 2.1.142 (newer, has optional fields).
    const SAMPLE_NEWER: &str = r#"{
        "state": "done",
        "detail": "answered 2+2=4 in one sentence",
        "tempo": "idle",
        "output": {"result": "2+2 = 4"},
        "children": null,
        "linkScanOffset": 17321,
        "template": "research",
        "respawnFlags": ["--agent", "research"],
        "intent": "What is 2+2? Answer in one sentence then stop.",
        "sessionId": "40726463-aaaa-bbbb-cccc-dddddddddddd",
        "resumeSessionId": "40726463-aaaa-bbbb-cccc-dddddddddddd",
        "daemonShort": "40726463",
        "cwd": "D:\\Mercury\\Mercury",
        "createdAt": "2026-05-14T16:42:55.110Z",
        "updatedAt": "2026-05-14T16:42:55.110Z",
        "firstTerminalAt": "2026-05-14T16:42:55.110Z",
        "backend": "daemon",
        "inFlight": {"tasks": 0, "queued": 0, "kinds": []},
        "linkScanPath": "C:\\Users\\fake\\.claude\\projects\\D--Mercury-Mercury\\40726463.jsonl",
        "cliVersion": "2.1.142",
        "name": "math calculation response",
        "nameSource": "auto"
    }"#;

    /// Sample from S120 ADR — sessionId `1baace78`, older session: missing inFlight / linkScanPath /
    /// cliVersion / name / nameSource entirely.
    const SAMPLE_OLDER: &str = r#"{
        "state": "done",
        "detail": "(idle — send a prompt to start)",
        "tempo": "idle",
        "output": null,
        "children": null,
        "linkScanOffset": 0,
        "template": "bg",
        "respawnFlags": ["--help"],
        "intent": "",
        "sessionId": "1baace78-6b3a-478b-829b-0def2187745c",
        "resumeSessionId": "1baace78-6b3a-478b-829b-0def2187745c",
        "daemonShort": "1baace78",
        "cwd": "D:\\Mercury\\Mercury",
        "createdAt": "2026-05-14T16:42:55.110Z",
        "updatedAt": "2026-05-14T16:42:55.110Z",
        "firstTerminalAt": "2026-05-14T16:42:55.110Z",
        "backend": "daemon"
    }"#;

    #[test]
    fn parses_newer_session_all_fields() {
        let job: JobState = serde_json::from_str(SAMPLE_NEWER).expect("newer parse");
        assert_eq!(job.state, "done");
        assert_eq!(job.template, "research");
        assert_eq!(job.cli_version.as_deref(), Some("2.1.142"));
        assert_eq!(job.name.as_deref(), Some("math calculation response"));
        assert!(job.in_flight.is_some());
        assert_eq!(job.in_flight.unwrap().tasks, 0);
        assert!(job.created_at.is_some());
    }

    #[test]
    fn parses_older_session_missing_optional_fields() {
        let job: JobState = serde_json::from_str(SAMPLE_OLDER).expect("older parse");
        assert_eq!(job.daemon_short, "1baace78");
        assert!(job.cli_version.is_none(), "cliVersion absent on older session");
        assert!(job.name.is_none(), "name absent on older session");
        assert!(job.name_source.is_none());
        assert!(job.in_flight.is_none());
        assert!(job.link_scan_path.is_none());
    }

    #[test]
    fn unknown_fields_ignored_silently() {
        let with_unknown = r#"{
            "state": "done",
            "detail": "",
            "tempo": "idle",
            "output": null,
            "children": null,
            "linkScanOffset": 0,
            "template": "bg",
            "respawnFlags": [],
            "intent": "",
            "sessionId": "x",
            "resumeSessionId": "x",
            "daemonShort": "x",
            "cwd": "/tmp",
            "createdAt": "2026-05-14T16:42:55.110Z",
            "updatedAt": "2026-05-14T16:42:55.110Z",
            "firstTerminalAt": "2026-05-14T16:42:55.110Z",
            "backend": "daemon",
            "futureField": "ignored"
        }"#;
        let job: JobState = serde_json::from_str(with_unknown).expect("unknown ok");
        assert_eq!(job.daemon_short, "x");
    }

    #[test]
    fn empty_object_yields_defaults() {
        // Schema-tolerant: missing required-by-ADR fields parse via #[serde(default)]
        // rather than panicking. GUI receives an empty-but-valid struct.
        let job: JobState = serde_json::from_str("{}").expect("empty parse");
        assert_eq!(job.state, "");
        assert_eq!(job.link_scan_offset, 0);
        assert!(job.created_at.is_none());
    }

    #[test]
    fn roster_unix_ms_dispatch() {
        let roster_json = r#"{
            "proto": 1,
            "supervisorPid": 2240,
            "updatedAt": 1778822198033,
            "workers": {}
        }"#;
        let roster: RosterSnapshot = serde_json::from_str(roster_json).expect("roster parse");
        assert_eq!(roster.proto, 1);
        assert_eq!(roster.supervisor_pid, 2240);
        assert!(roster.updated_at.is_some());
        // 1778822198033 ms → year 2026 (sanity check)
        assert!(roster.updated_at.unwrap().timestamp() > 1_700_000_000);
        assert!(roster.workers.is_empty());
    }

    #[test]
    fn roster_empty_workers_default() {
        let roster: RosterSnapshot = serde_json::from_str("{}").expect("empty roster");
        assert_eq!(roster.proto, 0);
        assert!(roster.workers.is_empty());
    }

    #[test]
    fn worktree_fields_absent_on_terminal() {
        let job: JobState = serde_json::from_str(SAMPLE_NEWER).expect("parse");
        assert!(job.worktree_path.is_none());
        assert!(job.worktree_branch.is_none());
    }

    #[test]
    fn sanitize_paths_scrubs_output_recursive() {
        let Some(home) = dirs::home_dir().and_then(|p| p.to_str().map(String::from)) else {
            return;
        };
        let mut job: JobState = serde_json::from_str(SAMPLE_NEWER).expect("parse");
        // Inject a nested JSON blob containing home paths into the `output` field.
        job.output = Some(serde_json::json!({
            "stdout": format!("wrote {home}/.claude/jobs/abc/state.json"),
            "files": [format!("{home}/.claude/a"), format!("{home}/.claude/b")]
        }));
        job.sanitize_paths();
        let out_str = serde_json::to_string(&job.output).unwrap();
        assert!(!out_str.contains(&home), "output home must be scrubbed recursively");
        assert!(out_str.contains("~/.claude/jobs/"));
        assert!(out_str.contains("~/.claude/a"));
        assert!(out_str.contains("~/.claude/b"));
    }

    #[test]
    fn sanitize_paths_scrubs_children_recursive() {
        let Some(home) = dirs::home_dir().and_then(|p| p.to_str().map(String::from)) else {
            return;
        };
        let mut job: JobState = serde_json::from_str(SAMPLE_NEWER).expect("parse");
        job.children = Some(serde_json::json!({
            "child_a": { "log_path": format!("{home}/log.txt") },
            "child_b": { "cwd": format!("{home}/work") }
        }));
        job.sanitize_paths();
        let children_str = serde_json::to_string(&job.children).unwrap();
        assert!(!children_str.contains(&home), "children home must be scrubbed recursively");
        assert!(children_str.contains("~/log.txt"));
        assert!(children_str.contains("~/work"));
    }

    #[test]
    fn sanitize_paths_is_idempotent() {
        // Calling sanitize_paths twice must produce equal output (struct-level contract).
        let Some(home) = dirs::home_dir().and_then(|p| p.to_str().map(String::from)) else {
            return;
        };
        let mut job: JobState = serde_json::from_str(SAMPLE_NEWER).expect("parse");
        job.output = Some(serde_json::json!({ "log": format!("{home}/log.txt") }));
        job.children = Some(serde_json::json!([format!("{home}/child")]));
        job.sanitize_paths();
        let after_first = (job.cwd.clone(), job.link_scan_path.clone(), job.worktree_path.clone(), job.output.clone(), job.children.clone());
        job.sanitize_paths();
        let after_second = (job.cwd.clone(), job.link_scan_path.clone(), job.worktree_path.clone(), job.output.clone(), job.children.clone());
        assert_eq!(after_first, after_second, "sanitize_paths must be idempotent");
    }

    #[test]
    fn sanitize_paths_noop_when_output_children_none() {
        // SAMPLE_OLDER has output: null + children: null. Sanitize must not panic.
        let mut job: JobState = serde_json::from_str(SAMPLE_OLDER).expect("parse");
        assert!(job.output.is_none() || job.output == Some(serde_json::Value::Null));
        assert!(job.children.is_none() || job.children == Some(serde_json::Value::Null));
        let cwd_before = job.cwd.clone();
        let link_scan_before = job.link_scan_path.clone();
        let worktree_before = job.worktree_path.clone();
        job.sanitize_paths(); // no panic
        // Assert via invariant rather than hardcoded literal: when the sample's `cwd`
        // does not start with the running process's home dir, sanitize_paths leaves
        // all path-bearing fields untouched. (SAMPLE_OLDER's cwd happens to be a
        // non-home test path; the assertion locks the no-op behavior without
        // depending on the literal value.)
        assert_eq!(job.cwd, cwd_before, "cwd unchanged when no home prefix matches");
        assert_eq!(job.link_scan_path, link_scan_before, "link_scan_path unchanged");
        assert_eq!(job.worktree_path, worktree_before, "worktree_path unchanged");
    }

    #[test]
    fn worktree_fields_present_on_midflight() {
        let mid_flight = r#"{
            "state": "working",
            "detail": "editing file",
            "tempo": "active",
            "output": null,
            "children": null,
            "linkScanOffset": 100,
            "template": "bg",
            "respawnFlags": [],
            "intent": "edit foo.txt",
            "sessionId": "abc",
            "resumeSessionId": "abc",
            "daemonShort": "abc",
            "cwd": "D:\\Mercury\\Mercury",
            "createdAt": "2026-05-14T16:42:55.110Z",
            "updatedAt": "2026-05-14T16:42:55.110Z",
            "firstTerminalAt": "2026-05-14T16:42:55.110Z",
            "backend": "daemon",
            "worktreePath": "D:\\Mercury\\Mercury\\.claude\\worktrees\\phase6-probe-1",
            "worktreeBranch": "phase6-probe-1"
        }"#;
        let job: JobState = serde_json::from_str(mid_flight).expect("mid-flight parse");
        assert_eq!(job.worktree_branch.as_deref(), Some("phase6-probe-1"));
    }
}
