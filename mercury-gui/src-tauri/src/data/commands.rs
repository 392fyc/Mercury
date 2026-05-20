use super::models::{JobState, Lane, RosterSnapshot};
use super::paths;
use super::DataError;
use std::collections::HashMap;
use std::fs;
use std::io::ErrorKind;

/// state.json files in the wild are typically 1-10 KB. Cap reads at 2 MB so a
/// pathological file can't OOM the GUI process (#414 Argus iter-1 Critical 5).
const MAX_STATE_JSON_BYTES: u64 = 2 * 1024 * 1024;

#[tauri::command]
pub fn read_jobs() -> Result<Vec<JobState>, DataError> {
    let dir = paths::jobs_dir();
    if !dir.exists() {
        return Ok(Vec::new());
    }
    let mut out = Vec::new();
    for entry in fs::read_dir(&dir)? {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let state_path = entry.path().join("state.json");
        if !state_path.is_file() {
            continue;
        }
        // Schema-tolerant: skip unreadable / malformed / oversized files rather
        // than failing the whole call. GUI surfaces "0 jobs" instead of an error
        // if every file is corrupt.
        let Ok(meta) = fs::metadata(&state_path) else { continue };
        if meta.len() > MAX_STATE_JSON_BYTES {
            continue;
        }
        let Ok(bytes) = fs::read(&state_path) else { continue };
        let Ok(mut job) = serde_json::from_slice::<JobState>(&bytes) else { continue };
        // Strip home prefix from path-bearing fields so the JS frontend never
        // sees `C:\Users\<name>\...` literals (defense in depth — `redact_home`
        // is also applied to surfaced error strings in DataError).
        job.sanitize_paths();
        out.push(job);
    }
    Ok(out)
}

#[tauri::command]
pub fn read_roster() -> Result<RosterSnapshot, DataError> {
    let p = paths::roster_path();
    if !p.exists() {
        return Ok(RosterSnapshot::default());
    }
    let bytes = match fs::read(&p) {
        Ok(b) => b,
        // Transient NotFound (e.g. supervisor file rotation) should not flash
        // an error to the GUI; degrade to default and resync on the next watcher
        // tick (matches the lenient pattern of read_jobs).
        Err(e) if e.kind() == ErrorKind::NotFound => return Ok(RosterSnapshot::default()),
        Err(e) => return Err(e.into()),
    };
    // roster.json is written by an external supervisor process; mid-write reads
    // can yield truncated JSON. Degrade to default rather than surfacing a
    // transient parse error.
    Ok(serde_json::from_slice(&bytes).unwrap_or_default())
}

#[tauri::command]
pub fn read_lanes() -> Result<Vec<Lane>, DataError> {
    let p = paths::lanes_path();
    if !p.exists() {
        return Ok(Vec::new());
    }
    let text = fs::read_to_string(&p)?;
    Ok(parse_lanes_md(&text))
}

#[tauri::command]
pub fn read_jobs_by_lane() -> Result<HashMap<String, Vec<JobState>>, DataError> {
    // Propagate lane-resolution failures so the GUI can surface a degraded state
    // explicitly instead of silently filing every job under `__unassigned__`.
    // `read_lanes` already returns `Ok(empty)` when LANES.md is simply absent.
    let jobs = read_jobs()?;
    let lanes = read_lanes()?;
    Ok(group_jobs_by_lane(jobs, &lanes))
}

/// Parse the `## Active Lanes` section of LANES.md.
///
/// Format (canonical per the user-memory file):
///   ## Active Lanes
///   ### `<name>` (... description ...)
///   - **Worktree path** (...): `<path>` — annotations
///   - **Status**: `<status>`
///   ...
///   ## Closed Lanes
///
/// The first backticked group on a worktree/status bullet is the value;
/// bullets without backticks (e.g. the `health` lane's N/A variant) yield `None`.
pub(crate) fn parse_lanes_md(text: &str) -> Vec<Lane> {
    let mut lanes: Vec<Lane> = Vec::new();
    let mut current: Option<Lane> = None;
    let mut in_active = false;

    for raw in text.lines() {
        let line = raw.trim_end();
        if let Some(stripped) = line.strip_prefix("## ") {
            if stripped.starts_with("Active") {
                in_active = true;
            } else {
                if let Some(l) = current.take() {
                    lanes.push(l);
                }
                in_active = false;
            }
            continue;
        }
        if !in_active {
            continue;
        }
        if line.starts_with("### ") {
            if let Some(l) = current.take() {
                lanes.push(l);
            }
            if let Some(name) = first_backticked(line) {
                current = Some(Lane {
                    name,
                    worktree_path: None,
                    status: None,
                });
            }
            continue;
        }
        let Some(ref mut lane) = current else { continue };
        let trimmed = line.trim_start();
        if trimmed.starts_with("- **Worktree path**") {
            lane.worktree_path = first_backticked(trimmed);
        } else if trimmed.starts_with("- **Status**") {
            lane.status = first_backticked(trimmed);
        }
    }
    if let Some(l) = current.take() {
        lanes.push(l);
    }
    lanes
}

/// First substring enclosed by a matched pair of backticks, or None.
fn first_backticked(s: &str) -> Option<String> {
    let start = s.find('`')?;
    let rest = &s[start + 1..];
    let end = rest.find('`')?;
    Some(rest[..end].to_string())
}

/// Group jobs by lane via case-insensitive normalized-path match of `cwd` against
/// `lane.worktree_path`. Jobs whose cwd doesn't match any lane fall under `__unassigned__`.
pub(crate) fn group_jobs_by_lane(
    jobs: Vec<JobState>,
    lanes: &[Lane],
) -> HashMap<String, Vec<JobState>> {
    let mut out: HashMap<String, Vec<JobState>> = HashMap::new();
    for lane in lanes {
        out.entry(lane.name.clone()).or_default();
    }
    for job in jobs {
        let job_norm = normalize_path(&job.cwd);
        let matched = lanes
            .iter()
            .find(|l| {
                l.worktree_path
                    .as_ref()
                    .map(|wp| normalize_path(wp) == job_norm)
                    .unwrap_or(false)
            })
            .map(|l| l.name.clone())
            .unwrap_or_else(|| "__unassigned__".to_string());
        out.entry(matched).or_default().push(job);
    }
    out
}

/// Normalize: backslashes → forward slashes, strip trailing slash, lowercase.
/// Path comparison only — not used for filesystem access.
fn normalize_path(s: &str) -> String {
    s.replace('\\', "/")
        .trim_end_matches('/')
        .to_ascii_lowercase()
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE_LANES_MD: &str = r#"# Mercury Lanes Registry

## Active Lanes

### `main` (default lane)

- **Handoff file**: `session-handoff.md`
- **Worktree path** (per Rule 5.1, Issue #342): `D:/Mercury/Mercury` — main lane canonical
- **Status**: `active`

### `side-bug` (peripheral bug-fix lane)

- **Handoff file**: `session-handoff-side-bug.md`
- **Worktree path** (per Rule 5.1): `D:/Mercury/Mercury-side-bug` (materialized 2026-05-04)
- **Status**: `active`

### `health` (personal health discussions)

- **Worktree path** (per Rule 5.1, **N/A variant**): **conversation-only lane — no git worktree**.
- **Status**: `active`

## Closed Lanes

### `side-multi-lane` (dedicated research lane) — CLOSED

- **Worktree path**: `D:/Mercury/Mercury-side-mlane`
- **Status**: `closed`
"#;

    #[test]
    fn parses_active_lanes_section_only() {
        let lanes = parse_lanes_md(SAMPLE_LANES_MD);
        assert_eq!(lanes.len(), 3, "active section: main + side-bug + health");
        assert_eq!(lanes[0].name, "main");
        assert_eq!(lanes[1].name, "side-bug");
        assert_eq!(lanes[2].name, "health");
    }

    #[test]
    fn extracts_worktree_path_when_backticked() {
        let lanes = parse_lanes_md(SAMPLE_LANES_MD);
        assert_eq!(
            lanes[0].worktree_path.as_deref(),
            Some("D:/Mercury/Mercury")
        );
        assert_eq!(
            lanes[1].worktree_path.as_deref(),
            Some("D:/Mercury/Mercury-side-bug")
        );
    }

    #[test]
    fn health_lane_worktree_is_none() {
        let lanes = parse_lanes_md(SAMPLE_LANES_MD);
        assert_eq!(lanes[2].name, "health");
        assert!(
            lanes[2].worktree_path.is_none(),
            "health lane has no backticked path → None"
        );
    }

    #[test]
    fn extracts_status() {
        let lanes = parse_lanes_md(SAMPLE_LANES_MD);
        assert_eq!(lanes[0].status.as_deref(), Some("active"));
        assert_eq!(lanes[2].status.as_deref(), Some("active"));
    }

    #[test]
    fn closed_lanes_section_excluded() {
        let lanes = parse_lanes_md(SAMPLE_LANES_MD);
        assert!(lanes.iter().all(|l| l.name != "side-multi-lane"));
    }

    #[test]
    fn empty_md_yields_no_lanes() {
        assert!(parse_lanes_md("").is_empty());
    }

    #[test]
    fn group_matches_windows_backslash_cwd_against_forward_slash_lane() {
        let job_windows_cwd = JobState {
            cwd: r"D:\Mercury\Mercury".to_string(),
            ..Default::default()
        };
        let lanes = vec![Lane {
            name: "main".to_string(),
            worktree_path: Some("D:/Mercury/Mercury".to_string()),
            status: Some("active".to_string()),
        }];
        let grouped = group_jobs_by_lane(vec![job_windows_cwd], &lanes);
        assert_eq!(grouped.get("main").map(Vec::len), Some(1));
        assert!(
            grouped.get("__unassigned__").map(Vec::is_empty).unwrap_or(true),
            "no unassigned bucket when all jobs match"
        );
    }

    #[test]
    fn group_unassigned_when_cwd_matches_no_lane() {
        let stray = JobState {
            cwd: r"D:\Other\Repo".to_string(),
            ..Default::default()
        };
        let lanes = vec![Lane {
            name: "main".to_string(),
            worktree_path: Some("D:/Mercury/Mercury".to_string()),
            status: None,
        }];
        let grouped = group_jobs_by_lane(vec![stray], &lanes);
        assert_eq!(grouped.get("__unassigned__").map(Vec::len), Some(1));
    }

    #[test]
    fn group_handles_lane_with_no_worktree_path() {
        let job = JobState {
            cwd: r"D:\Mercury\Mercury".to_string(),
            ..Default::default()
        };
        let lanes = vec![
            Lane {
                name: "health".to_string(),
                worktree_path: None,
                status: Some("active".to_string()),
            },
            Lane {
                name: "main".to_string(),
                worktree_path: Some("D:/Mercury/Mercury".to_string()),
                status: Some("active".to_string()),
            },
        ];
        let grouped = group_jobs_by_lane(vec![job], &lanes);
        assert_eq!(grouped.get("main").map(Vec::len), Some(1));
        assert_eq!(grouped.get("health").map(Vec::len), Some(0));
    }
}
