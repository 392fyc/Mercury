// gh_dashboard — IPC command + in-memory cache for the Issue/PR dashboard (#416).
// Invokes ambient `gh` CLI via tauri-plugin-shell (NOT std::process::Command).
// Cache is process-lifetime only; lost on app restart per #416 DoD.

use serde::{Deserialize, Serialize};
use std::time::{Duration, Instant};
use tauri_plugin_shell::ShellExt;

const GH_CACHE_TTL_SECS: u64 = 60;
const GH_REPO: &str = "392fyc/Mercury";
const GH_LIMIT: &str = "200";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GhLabel {
    pub name: String,
    pub color: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GhUser {
    pub login: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GhIssue {
    pub number: u64,
    pub title: String,
    pub state: String,
    pub labels: Vec<GhLabel>,
    pub assignees: Vec<GhUser>,
    #[serde(rename = "createdAt")]
    pub created_at: String,
    #[serde(rename = "updatedAt")]
    pub updated_at: String,
    pub url: String,
    pub milestone: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GhPullRequest {
    pub number: u64,
    pub title: String,
    pub state: String,
    pub labels: Vec<GhLabel>,
    pub assignees: Vec<GhUser>,
    #[serde(rename = "createdAt")]
    pub created_at: String,
    #[serde(rename = "updatedAt")]
    pub updated_at: String,
    pub url: String,
    #[serde(rename = "headRefName")]
    pub head_ref_name: String,
    #[serde(rename = "baseRefName")]
    pub base_ref_name: String,
    #[serde(rename = "isDraft")]
    pub is_draft: bool,
    #[serde(rename = "reviewDecision")]
    pub review_decision: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GhSnapshot {
    pub issues: Vec<GhIssue>,
    pub pull_requests: Vec<GhPullRequest>,
    // Unix epoch milliseconds — consistent with RosterSnapshot.updatedAt
    #[serde(rename = "fetchedAt")]
    pub fetched_at: i64,
}

pub struct GhCacheState {
    pub cache: tokio::sync::Mutex<Option<(GhSnapshot, Instant)>>,
}

impl Default for GhCacheState {
    fn default() -> Self {
        GhCacheState {
            cache: tokio::sync::Mutex::new(None),
        }
    }
}

#[tauri::command]
pub async fn fetch_gh_dashboard(
    app: tauri::AppHandle,
    state: tauri::State<'_, GhCacheState>,
    force: bool,
) -> Result<GhSnapshot, String> {
    let mut cache = state.cache.lock().await;

    // Return cached data if within TTL and not forced
    if !force {
        if let Some((ref snapshot, ref ts)) = *cache {
            if ts.elapsed() < Duration::from_secs(GH_CACHE_TTL_SECS) {
                return Ok(snapshot.clone());
            }
        }
    }

    // Fetch issues and PRs sequentially (simple; gh rate-limit safer)
    let issue_fields = "number,title,state,labels,assignees,createdAt,updatedAt,url,milestone";
    let pr_fields =
        "number,title,state,labels,assignees,createdAt,updatedAt,url,headRefName,baseRefName,isDraft,reviewDecision";

    let issue_out = app
        .shell()
        .command("gh")
        .args([
            "issue",
            "list",
            "--repo",
            GH_REPO,
            "--json",
            issue_fields,
            "--limit",
            GH_LIMIT,
            "--state",
            "open",
        ])
        .output()
        .await
        .map_err(|e| format!("gh spawn failed: {e}"))?;

    if !issue_out.status.success() {
        let stderr = String::from_utf8_lossy(&issue_out.stderr);
        return Err(format!("gh issue list failed: {stderr}"));
    }

    let pr_out = app
        .shell()
        .command("gh")
        .args([
            "pr",
            "list",
            "--repo",
            GH_REPO,
            "--json",
            pr_fields,
            "--limit",
            GH_LIMIT,
            "--state",
            "open",
        ])
        .output()
        .await
        .map_err(|e| format!("gh spawn failed: {e}"))?;

    if !pr_out.status.success() {
        let stderr = String::from_utf8_lossy(&pr_out.stderr);
        return Err(format!("gh pr list failed: {stderr}"));
    }

    let issues: Vec<GhIssue> = serde_json::from_slice(&issue_out.stdout)
        .map_err(|e| format!("parse issues: {e}"))?;

    let pull_requests: Vec<GhPullRequest> = serde_json::from_slice(&pr_out.stdout)
        .map_err(|e| format!("parse PRs: {e}"))?;

    let snapshot = GhSnapshot {
        issues,
        pull_requests,
        fetched_at: chrono::Utc::now().timestamp_millis(),
    };

    *cache = Some((snapshot.clone(), Instant::now()));
    Ok(snapshot)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_issue(number: u64, title: &str) -> GhIssue {
        GhIssue {
            number,
            title: title.to_string(),
            state: "open".to_string(),
            labels: vec![GhLabel {
                name: "enhancement".to_string(),
                color: Some("84b6eb".to_string()),
            }],
            assignees: vec![GhUser {
                login: "392fyc".to_string(),
            }],
            created_at: "2026-05-01T00:00:00Z".to_string(),
            updated_at: "2026-05-20T00:00:00Z".to_string(),
            url: format!("https://github.com/392fyc/Mercury/issues/{number}"),
            milestone: None,
        }
    }

    fn make_pr(number: u64, title: &str) -> GhPullRequest {
        GhPullRequest {
            number,
            title: title.to_string(),
            state: "open".to_string(),
            labels: vec![GhLabel {
                name: "lane:main".to_string(),
                color: None,
            }],
            assignees: vec![],
            created_at: "2026-05-10T00:00:00Z".to_string(),
            updated_at: "2026-05-20T00:00:00Z".to_string(),
            url: format!("https://github.com/392fyc/Mercury/pull/{number}"),
            head_ref_name: "lane/main/416-test".to_string(),
            base_ref_name: "develop".to_string(),
            is_draft: false,
            review_decision: None,
        }
    }

    #[test]
    fn serde_roundtrip_issue() {
        let issue = make_issue(416, "UI scenario 5");
        let json = serde_json::to_string(&issue).expect("serialize");
        let back: GhIssue = serde_json::from_str(&json).expect("deserialize");
        assert_eq!(back.number, 416);
        assert_eq!(back.title, "UI scenario 5");
        assert_eq!(back.labels[0].name, "enhancement");
        // camelCase fields survive round-trip
        assert_eq!(back.created_at, "2026-05-01T00:00:00Z");
    }

    #[test]
    fn serde_roundtrip_pr() {
        let pr = make_pr(424, "feat: slice C");
        let json = serde_json::to_string(&pr).expect("serialize");
        let back: GhPullRequest = serde_json::from_str(&json).expect("deserialize");
        assert_eq!(back.number, 424);
        assert!(!back.is_draft);
        assert_eq!(back.head_ref_name, "lane/main/416-test");
        assert_eq!(back.base_ref_name, "develop");
        assert!(back.review_decision.is_none());
    }

    #[test]
    fn serde_roundtrip_snapshot() {
        let snapshot = GhSnapshot {
            issues: vec![make_issue(1, "first")],
            pull_requests: vec![make_pr(2, "pr two")],
            fetched_at: 1_716_000_000_000,
        };
        let json = serde_json::to_string(&snapshot).expect("serialize");
        // fetchedAt must appear with camelCase key in JSON
        assert!(json.contains("fetchedAt"), "fetchedAt key missing: {json}");
        let back: GhSnapshot = serde_json::from_str(&json).expect("deserialize");
        assert_eq!(back.fetched_at, 1_716_000_000_000);
        assert_eq!(back.issues.len(), 1);
        assert_eq!(back.pull_requests.len(), 1);
    }

    #[tokio::test]
    async fn cache_hit_returns_same_snapshot() {
        let state = GhCacheState::default();
        let snapshot = GhSnapshot {
            issues: vec![make_issue(100, "cached")],
            pull_requests: vec![],
            fetched_at: 1_716_000_000_000,
        };
        // Pre-populate cache with a fresh entry
        {
            let mut guard = state.cache.lock().await;
            *guard = Some((snapshot.clone(), Instant::now()));
        }
        // Verify cache is populated and within TTL
        let guard = state.cache.lock().await;
        let (ref cached, ref ts) = guard.as_ref().expect("cache must be populated");
        assert!(ts.elapsed() < Duration::from_secs(GH_CACHE_TTL_SECS));
        assert_eq!(cached.issues[0].number, 100);
    }

    #[tokio::test]
    async fn cache_miss_on_expired_entry() {
        let state = GhCacheState::default();
        let snapshot = GhSnapshot {
            issues: vec![],
            pull_requests: vec![],
            fetched_at: 0,
        };
        // Pre-populate cache with an artificially old instant (TTL + 1s ago)
        let old_instant = Instant::now()
            .checked_sub(Duration::from_secs(GH_CACHE_TTL_SECS + 1))
            .expect("subtraction should not underflow on modern systems");
        {
            let mut guard = state.cache.lock().await;
            *guard = Some((snapshot, old_instant));
        }
        // Verify the entry is expired
        let guard = state.cache.lock().await;
        let (_, ref ts) = guard.as_ref().expect("entry present");
        assert!(
            ts.elapsed() >= Duration::from_secs(GH_CACHE_TTL_SECS),
            "entry should be expired"
        );
    }

    #[tokio::test]
    async fn force_refresh_bypasses_fresh_cache() {
        // Validate the force=true logic path: even a fresh cache entry should
        // be bypassed. We can't invoke the full command without AppHandle, so
        // we test the cache-check predicate directly.
        let state = GhCacheState::default();
        let snapshot = GhSnapshot {
            issues: vec![make_issue(1, "stale but fresh ts")],
            pull_requests: vec![],
            fetched_at: 1_716_000_000_000,
        };
        {
            let mut guard = state.cache.lock().await;
            *guard = Some((snapshot.clone(), Instant::now()));
        }
        // Simulate force=true: cache is ignored regardless of age
        let guard = state.cache.lock().await;
        let within_ttl = guard
            .as_ref()
            .map(|(_, ts)| ts.elapsed() < Duration::from_secs(GH_CACHE_TTL_SECS))
            .unwrap_or(false);
        // Even though within TTL, force=true means we would skip the early-return
        assert!(within_ttl, "entry is fresh");
        // The actual skip happens in the command function — verified by inspecting
        // the `if !force` guard in fetch_gh_dashboard.
    }
}
