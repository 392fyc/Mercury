// gh_dashboard — IPC command + in-memory cache for the Issue/PR dashboard (#416).
// Invokes ambient `gh` CLI via tauri-plugin-shell. Cache is process-lifetime
// only; lost on app restart per #416 DoD.

use serde::{Deserialize, Serialize};
use std::time::{Duration, Instant};
use tauri_plugin_shell::ShellExt;

use crate::data::redact_home;

const GH_CACHE_TTL_SECS: u64 = 60;
const GH_REPO_DEFAULT: &str = "392fyc/Mercury";
const GH_LIMIT: &str = "200";
const GH_SUBPROCESS_TIMEOUT_SECS: u64 = 30;
const GH_AUTH_HOSTNAME_DEFAULT: &str = "github.com";

/// Resolve the gh-host to preflight against. `MERCURY_GH_HOST` env override
/// supports GitHub Enterprise / multi-host deployments; falls back to
/// `github.com` when unset or when the override fails the hostname validator
/// (which mirrors the capability config's `^[\w.-]+$` slot).
fn resolve_gh_auth_hostname() -> String {
    std::env::var("MERCURY_GH_HOST")
        .ok()
        .filter(|h| is_valid_hostname(h))
        .unwrap_or_else(|| GH_AUTH_HOSTNAME_DEFAULT.to_string())
}

fn is_valid_hostname(s: &str) -> bool {
    !s.is_empty()
        && s.chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.')
}

/// Resolve the target gh repo. Defaults to 392fyc/Mercury (the #416 DoD repo).
/// `MERCURY_GH_REPO` env override allows running the GUI against a different
/// repo (e.g. for development against a fork). Validates `owner/repo` shape
/// to keep the shell arg surface tight even though the capability already
/// regex-checks the slot.
fn resolve_repo() -> Result<String, String> {
    let raw = std::env::var("MERCURY_GH_REPO")
        .unwrap_or_else(|_| GH_REPO_DEFAULT.to_string());
    if is_valid_repo(&raw) {
        Ok(raw)
    } else {
        Err(format!(
            "invalid MERCURY_GH_REPO {raw:?}, expected owner/repo with [\\w.-] segments"
        ))
    }
}

fn is_valid_repo(s: &str) -> bool {
    let mut parts = s.split('/');
    let owner = parts.next().unwrap_or("");
    let name = parts.next().unwrap_or("");
    parts.next().is_none()
        && !owner.is_empty()
        && !name.is_empty()
        && owner
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.')
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.')
}

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

/// Preflight result for `gh auth status` (#434).
/// `authenticated == false` means the dashboard fetch will fail; the frontend
/// surfaces an actionable "run `gh auth login`" toast instead of letting the
/// stderr passthrough from `gh issue list` reach the user as a generic error.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GhAuthStatus {
    pub authenticated: bool,
    pub hostname: String,
    pub account: Option<String>,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GhSnapshot {
    pub issues: Vec<GhIssue>,
    #[serde(rename = "pullRequests")]
    pub pull_requests: Vec<GhPullRequest>,
    // Unix epoch milliseconds — consistent with RosterSnapshot.updatedAt.
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

/// Cache-hit predicate. Returns a cloned snapshot when the entry is fresh and
/// `force` is false; otherwise returns None (caller must refetch).
/// Extracted so the force-bypass + TTL-expiry branches are directly testable
/// without an AppHandle.
fn check_cache_hit(
    entry: &Option<(GhSnapshot, Instant)>,
    force: bool,
    now: Instant,
    ttl: Duration,
) -> Option<GhSnapshot> {
    if force {
        return None;
    }
    let (snapshot, cached_at) = entry.as_ref()?;
    if now.saturating_duration_since(*cached_at) < ttl {
        Some(snapshot.clone())
    } else {
        None
    }
}

#[tauri::command]
pub async fn fetch_gh_dashboard(
    app: tauri::AppHandle,
    state: tauri::State<'_, GhCacheState>,
    force: bool,
) -> Result<GhSnapshot, String> {
    // Acquire the lock only long enough to read the cache, then drop it
    // before spawning gh. Holding it across the subprocess await would
    // serialize concurrent IPC calls behind whichever gh invocation is
    // currently in flight, defeating the cache and risking an indefinite
    // hang if gh stalls (M1 from dual-verify).
    let ttl = Duration::from_secs(GH_CACHE_TTL_SECS);
    let timeout = Duration::from_secs(GH_SUBPROCESS_TIMEOUT_SECS);
    {
        let cache = state.cache.lock().await;
        if let Some(hit) = check_cache_hit(&cache, force, Instant::now(), ttl) {
            return Ok(hit);
        }
    }

    let repo = resolve_repo()?;
    let issue_fields = "number,title,state,labels,assignees,createdAt,updatedAt,url,milestone";
    let pr_fields =
        "number,title,state,labels,assignees,createdAt,updatedAt,url,headRefName,baseRefName,isDraft,reviewDecision";

    let issue_fut = app
        .shell()
        .command("gh")
        .args([
            "issue",
            "list",
            "--repo",
            repo.as_str(),
            "--json",
            issue_fields,
            "--limit",
            GH_LIMIT,
            "--state",
            "open",
        ])
        .output();
    let issue_out = tokio::time::timeout(timeout, issue_fut)
        .await
        .map_err(|_| {
            format!(
                "gh issue list timed out after {GH_SUBPROCESS_TIMEOUT_SECS}s — check `gh auth status` and network connectivity"
            )
        })?
        .map_err(|e| redact_home(&format!("gh spawn failed: {e}")))?;

    if !issue_out.status.success() {
        let stderr = String::from_utf8_lossy(&issue_out.stderr);
        return Err(redact_home(&format!("gh issue list failed: {stderr}")));
    }

    let pr_fut = app
        .shell()
        .command("gh")
        .args([
            "pr",
            "list",
            "--repo",
            repo.as_str(),
            "--json",
            pr_fields,
            "--limit",
            GH_LIMIT,
            "--state",
            "open",
        ])
        .output();
    let pr_out = tokio::time::timeout(timeout, pr_fut)
        .await
        .map_err(|_| {
            format!(
                "gh pr list timed out after {GH_SUBPROCESS_TIMEOUT_SECS}s — check `gh auth status` and network connectivity"
            )
        })?
        .map_err(|e| redact_home(&format!("gh spawn failed: {e}")))?;

    if !pr_out.status.success() {
        let stderr = String::from_utf8_lossy(&pr_out.stderr);
        return Err(redact_home(&format!("gh pr list failed: {stderr}")));
    }

    let issues: Vec<GhIssue> = serde_json::from_slice(&issue_out.stdout)
        .map_err(|e| redact_home(&format!("parse issues: {e}")))?;

    let pull_requests: Vec<GhPullRequest> = serde_json::from_slice(&pr_out.stdout)
        .map_err(|e| redact_home(&format!("parse PRs: {e}")))?;

    let snapshot = GhSnapshot {
        issues,
        pull_requests,
        fetched_at: chrono::Utc::now().timestamp_millis(),
    };

    let mut cache = state.cache.lock().await;
    *cache = Some((snapshot.clone(), Instant::now()));
    Ok(snapshot)
}

/// Parse a `gh auth status` output blob for the active account login.
/// Looks for the canonical `" account <login>"` marker emitted by gh CLI
/// (verified against gh v2.87.3, 2026-02-23 on the success path:
/// `✓ Logged in to github.com account <login> (keyring)`). Returns the first
/// whitespace-delimited token following the marker.
///
/// SAFETY: This is a permissive marker-scan — `" account configured"` would
/// match and return `"configured"`. The caller MUST gate on the success
/// path (exit code 0) before relying on this output (see `check_gh_auth`,
/// where `account` is only extracted when `authenticated == true` so the
/// canonical gh success line is the only realistic input).
fn parse_gh_account(text: &str) -> Option<String> {
    let marker = " account ";
    let idx = text.find(marker)?;
    let after = &text[idx + marker.len()..];
    after.split_whitespace().next().map(String::from)
}

/// Preflight `gh auth status` check (#434). Runs BEFORE the dashboard's
/// issue/PR list calls so the frontend can surface a clear "run `gh auth login`"
/// toast instead of letting the deeper `gh issue list` stderr passthrough reach
/// the user as a generic fetch error.
///
/// Exit-code contract per `cli.github.com/manual/gh_auth_status`:
///   * exit 0 → authenticated (output to stdout)
///   * exit 1 → at least one account has auth issues (output to stderr)
///
/// `--hostname github.com` constrains the check to the dashboard's target host
/// so an unrelated host's broken token does not falsely flag the active one.
/// Both streams are combined for the account-marker scan; only stderr is
/// surfaced in the failure `message` so the toast text stays actionable.
#[tauri::command]
pub async fn check_gh_auth(app: tauri::AppHandle) -> Result<GhAuthStatus, String> {
    let timeout = Duration::from_secs(GH_SUBPROCESS_TIMEOUT_SECS);
    let hostname = resolve_gh_auth_hostname();
    let fut = app
        .shell()
        .command("gh")
        .args(["auth", "status", "--hostname", hostname.as_str()])
        .output();
    let out = tokio::time::timeout(timeout, fut)
        .await
        .map_err(|_| {
            format!(
                "gh auth status timed out after {GH_SUBPROCESS_TIMEOUT_SECS}s — gh CLI may be missing or hanging"
            )
        })?
        .map_err(|e| redact_home(&format!(
            "gh spawn failed: {e} — ensure `gh` CLI is installed and on PATH"
        )))?;

    let stdout = String::from_utf8_lossy(&out.stdout);
    let stderr = String::from_utf8_lossy(&out.stderr);
    let authenticated = out.status.success();
    let combined = format!("{stdout}\n{stderr}");
    let account = if authenticated {
        parse_gh_account(&combined)
    } else {
        None
    };
    let message = redact_home(
        if authenticated {
            combined.trim()
        } else {
            stderr.trim()
        },
    );

    Ok(GhAuthStatus {
        authenticated,
        hostname,
        account,
        message,
    })
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

    fn make_snapshot() -> GhSnapshot {
        GhSnapshot {
            issues: vec![make_issue(1, "first")],
            pull_requests: vec![make_pr(2, "pr two")],
            fetched_at: 1_716_000_000_000,
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
    fn serde_snapshot_uses_camelcase_keys() {
        let json = serde_json::to_string(&make_snapshot()).expect("serialize");
        assert!(json.contains("\"pullRequests\""), "pullRequests key missing: {json}");
        assert!(json.contains("\"fetchedAt\""), "fetchedAt key missing: {json}");
        assert!(!json.contains("\"pull_requests\""), "snake_case must not appear");
        let back: GhSnapshot = serde_json::from_str(&json).expect("deserialize");
        assert_eq!(back.fetched_at, 1_716_000_000_000);
        assert_eq!(back.issues.len(), 1);
        assert_eq!(back.pull_requests.len(), 1);
    }

    #[test]
    fn cache_hit_when_fresh_and_not_forced() {
        let snapshot = make_snapshot();
        let cached_at = Instant::now();
        let entry = Some((snapshot.clone(), cached_at));
        let now = cached_at + Duration::from_secs(30); // within 60s TTL
        let hit = check_cache_hit(&entry, false, now, Duration::from_secs(60));
        assert!(hit.is_some(), "fresh non-forced lookup must hit");
        assert_eq!(hit.unwrap().issues.len(), 1);
    }

    #[test]
    fn cache_miss_when_force_even_if_fresh() {
        // Real coverage for the previously-vestigial force-bypass branch.
        let snapshot = make_snapshot();
        let cached_at = Instant::now();
        let entry = Some((snapshot, cached_at));
        let now = cached_at + Duration::from_secs(1); // very fresh
        let hit = check_cache_hit(&entry, true, now, Duration::from_secs(60));
        assert!(hit.is_none(), "force=true must bypass fresh cache");
    }

    #[test]
    fn cache_miss_when_expired() {
        let snapshot = make_snapshot();
        let cached_at = Instant::now();
        let entry = Some((snapshot, cached_at));
        let now = cached_at + Duration::from_secs(61); // past 60s TTL
        let hit = check_cache_hit(&entry, false, now, Duration::from_secs(60));
        assert!(hit.is_none(), "expired entry must miss");
    }

    #[test]
    fn cache_miss_when_empty() {
        let entry: Option<(GhSnapshot, Instant)> = None;
        let hit = check_cache_hit(&entry, false, Instant::now(), Duration::from_secs(60));
        assert!(hit.is_none(), "empty cache must miss");
    }

    #[test]
    fn parse_gh_account_extracts_login_from_success_output() {
        // Canonical gh v2.87.3 success output shape.
        let sample = "github.com\n  ✓ Logged in to github.com account 392fyc (keyring)\n  - Active account: true\n  - Git operations protocol: https\n";
        assert_eq!(parse_gh_account(sample), Some("392fyc".to_string()));
    }

    #[test]
    fn parse_gh_account_returns_none_on_failure_text() {
        // gh stderr text when no host is logged in.
        let sample = "You are not logged into any GitHub hosts. To log in, run: gh auth login\n";
        assert_eq!(parse_gh_account(sample), None);
    }

    #[test]
    fn parse_gh_account_first_match_wins_on_multi_host() {
        // Multi-host output (hypothetical) — first " account <login>" wins.
        let sample = "github.com\n  ✓ Logged in to github.com account first-user (keyring)\nenterprise.example.com\n  ✓ Logged in to enterprise.example.com account second-user (keyring)\n";
        assert_eq!(parse_gh_account(sample), Some("first-user".to_string()));
    }

    #[test]
    fn parse_gh_account_handles_empty_input() {
        assert_eq!(parse_gh_account(""), None);
    }

    #[test]
    fn parse_gh_account_documents_permissive_false_match() {
        // Lock-in test for the documented "permissive marker-scan" contract:
        // adversarial text containing " account " followed by a token returns
        // that token. This is intentional — the caller (check_gh_auth) gates
        // on exit code 0 so this branch is unreachable in practice. If a
        // future refactor moves parse_gh_account outside the success gate,
        // THIS test fails as a tripwire and forces a parser tightening.
        let sample = "warning: no account configured for upstream";
        assert_eq!(
            parse_gh_account(sample),
            Some("configured".to_string()),
            "parser is intentionally permissive; safety relies on success-path gating"
        );
    }

    #[test]
    fn is_valid_hostname_accepts_canonical() {
        assert!(is_valid_hostname("github.com"));
        assert!(is_valid_hostname("ghe.example.com"));
        assert!(is_valid_hostname("internal-ghe-01"));
        assert!(is_valid_hostname("host_with_underscore"));
    }

    #[test]
    fn is_valid_hostname_rejects_malformed() {
        assert!(!is_valid_hostname(""));
        assert!(!is_valid_hostname("has space"));
        assert!(!is_valid_hostname("evil;rm -rf /"));
        assert!(!is_valid_hostname("host/path"));
        assert!(!is_valid_hostname("host:port"));
    }

    #[test]
    fn resolve_gh_auth_hostname_falls_back_when_unset() {
        // Note: this test reads the live env var. CI / dev shells typically
        // don't set MERCURY_GH_HOST, so the fallback path is exercised.
        // If a developer happens to have it set, the test asserts that the
        // resolver returned a non-empty hostname (the env value, if valid,
        // or the default).
        let resolved = resolve_gh_auth_hostname();
        assert!(!resolved.is_empty());
        assert!(is_valid_hostname(&resolved));
    }

    #[test]
    fn serde_roundtrip_gh_auth_status_authenticated() {
        let status = GhAuthStatus {
            authenticated: true,
            hostname: "github.com".to_string(),
            account: Some("392fyc".to_string()),
            message: "github.com\n  ✓ Logged in to github.com account 392fyc (keyring)".to_string(),
        };
        let json = serde_json::to_string(&status).expect("serialize");
        let back: GhAuthStatus = serde_json::from_str(&json).expect("deserialize");
        assert!(back.authenticated);
        assert_eq!(back.hostname, "github.com");
        assert_eq!(back.account.as_deref(), Some("392fyc"));
        assert!(back.message.contains("392fyc"));
    }

    #[test]
    fn serde_roundtrip_gh_auth_status_unauthenticated() {
        let status = GhAuthStatus {
            authenticated: false,
            hostname: "github.com".to_string(),
            account: None,
            message: "You are not logged into any GitHub hosts.".to_string(),
        };
        let json = serde_json::to_string(&status).expect("serialize");
        let back: GhAuthStatus = serde_json::from_str(&json).expect("deserialize");
        assert!(!back.authenticated);
        assert!(back.account.is_none());
        assert!(back.message.contains("not logged"));
    }

    #[test]
    fn is_valid_repo_accepts_canonical() {
        assert!(is_valid_repo("392fyc/Mercury"));
        assert!(is_valid_repo("owner-name/repo.name"));
        assert!(is_valid_repo("a/b"));
    }

    #[test]
    fn is_valid_repo_rejects_malformed() {
        assert!(!is_valid_repo(""));
        assert!(!is_valid_repo("nonslash"));
        assert!(!is_valid_repo("/leading-slash"));
        assert!(!is_valid_repo("trailing/"));
        assert!(!is_valid_repo("too/many/slashes"));
        assert!(!is_valid_repo("owner/repo;rm -rf /"));
        assert!(!is_valid_repo("owner repo/name"));
    }
}
