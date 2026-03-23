use serde::{Deserialize, Serialize};
use std::process::Command;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use tauri::Emitter;
use tokio::sync::Mutex;

// ─── Data Model ───

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PrReview {
    pub author: String,
    pub state: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CodeRabbitStatus {
    Pending,
    Commented,
    Approved,
    ChangesRequested,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PullRequest {
    pub number: u32,
    pub title: String,
    pub head_ref_name: String,
    pub author: String,
    pub created_at: String,
    pub updated_at: String,
    pub url: String,
    pub review_decision: Option<String>,
    pub coderabbit_status: CodeRabbitStatus,
    pub timeout_alert: bool,
    pub reviews: Vec<PrReview>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PrMonitorState {
    pub polling: bool,
    pub interval_secs: u64,
    pub prs: Vec<PullRequest>,
    pub last_error: Option<String>,
    pub last_fetched_at: Option<String>,
}

// ─── Monitor Implementation ───

pub struct PrMonitor {
    project_root: Arc<Mutex<String>>,
    polling: Arc<AtomicBool>,
    generation: Arc<AtomicU64>,
    interval_secs: Arc<Mutex<u64>>,
    prs: Arc<Mutex<Vec<PullRequest>>>,
    last_error: Arc<Mutex<Option<String>>>,
    last_fetched_at: Arc<Mutex<Option<String>>>,
}

impl PrMonitor {
    pub fn new(project_root: String) -> Self {
        Self {
            project_root: Arc::new(Mutex::new(project_root)),
            polling: Arc::new(AtomicBool::new(false)),
            generation: Arc::new(AtomicU64::new(0)),
            interval_secs: Arc::new(Mutex::new(60)),
            prs: Arc::new(Mutex::new(Vec::new())),
            last_error: Arc::new(Mutex::new(None)),
            last_fetched_at: Arc::new(Mutex::new(None)),
        }
    }

    pub async fn set_project_root(&self, root: String) {
        let mut guard = self.project_root.lock().await;
        *guard = root;
    }

    /// Fetch open PRs once via `gh pr list --json ...`.
    pub async fn fetch_prs(&self) -> Result<Vec<PullRequest>, String> {
        let root = self.project_root.lock().await.clone();
        if root.is_empty() {
            return Err("Project root not set".to_string());
        }

        let prs = tokio::task::spawn_blocking(move || fetch_prs_blocking(&root))
            .await
            .map_err(|e| format!("Task join error: {}", e))??;

        // Update cached state
        {
            let mut guard = self.prs.lock().await;
            *guard = prs.clone();
        }
        {
            let mut guard = self.last_error.lock().await;
            *guard = None;
        }
        {
            let mut guard = self.last_fetched_at.lock().await;
            *guard = Some(now_unix_ms());
        }

        Ok(prs)
    }

    /// Start background polling. Emits `pr-list-updated` events on changes.
    pub async fn start_polling(
        &self,
        app: tauri::AppHandle,
        interval_secs: Option<u64>,
    ) -> Result<(), String> {
        if self.polling.load(Ordering::SeqCst) {
            return Err("Polling already active".to_string());
        }

        let interval = interval_secs.unwrap_or(60);
        {
            let mut guard = self.interval_secs.lock().await;
            *guard = interval;
        }

        self.polling.store(true, Ordering::SeqCst);
        let gen = self.generation.fetch_add(1, Ordering::SeqCst) + 1;

        let polling_flag = self.polling.clone();
        let generation = self.generation.clone();
        let project_root = self.project_root.clone();
        let prs_cache = self.prs.clone();
        let last_error = self.last_error.clone();
        let last_fetched_at = self.last_fetched_at.clone();

        tokio::spawn(async move {
            loop {
                // Check if this generation is still current
                if generation.load(Ordering::SeqCst) != gen
                    || !polling_flag.load(Ordering::SeqCst)
                {
                    break;
                }

                let root = project_root.lock().await.clone();
                if root.is_empty() {
                    tokio::time::sleep(std::time::Duration::from_secs(interval)).await;
                    continue;
                }

                let root_clone = root.clone();
                let result =
                    tokio::task::spawn_blocking(move || fetch_prs_blocking(&root_clone)).await;

                // Check generation again after blocking work
                if generation.load(Ordering::SeqCst) != gen
                    || !polling_flag.load(Ordering::SeqCst)
                {
                    break;
                }

                match result {
                    Ok(Ok(new_prs)) => {
                        let changed = {
                            let mut guard = prs_cache.lock().await;
                            let changed = *guard != new_prs;
                            *guard = new_prs.clone();
                            changed
                        };
                        {
                            let mut guard = last_error.lock().await;
                            *guard = None;
                        }
                        {
                            let mut guard = last_fetched_at.lock().await;
                            *guard = Some(now_unix_ms());
                        }

                        if changed {
                            let _ = app.emit(
                                "pr-list-updated",
                                serde_json::json!({
                                    "prs": new_prs,
                                    "timestamp": now_unix_ms(),
                                }),
                            );
                        }
                    }
                    Ok(Err(e)) => {
                        let mut guard = last_error.lock().await;
                        *guard = Some(e.clone());
                        eprintln!("[pr-monitor] fetch error: {}", e);
                    }
                    Err(e) => {
                        let mut guard = last_error.lock().await;
                        *guard = Some(format!("Task panic: {}", e));
                    }
                }

                tokio::time::sleep(std::time::Duration::from_secs(interval)).await;
            }
            eprintln!("[pr-monitor] polling loop exited (gen={})", gen);
        });

        Ok(())
    }

    pub fn stop_polling(&self) {
        self.polling.store(false, Ordering::SeqCst);
    }

    pub fn is_polling(&self) -> bool {
        self.polling.load(Ordering::SeqCst)
    }

    pub async fn get_state(&self) -> PrMonitorState {
        PrMonitorState {
            polling: self.polling.load(Ordering::SeqCst),
            interval_secs: *self.interval_secs.lock().await,
            prs: self.prs.lock().await.clone(),
            last_error: self.last_error.lock().await.clone(),
            last_fetched_at: self.last_fetched_at.lock().await.clone(),
        }
    }
}

// ─── Blocking helpers (run via spawn_blocking) ───

/// Raw JSON shape returned by `gh pr list --json ...`.
#[derive(Deserialize)]
struct GhPr {
    number: u32,
    title: String,
    #[serde(rename = "headRefName")]
    head_ref_name: String,
    author: GhAuthor,
    #[serde(rename = "createdAt")]
    created_at: String,
    #[serde(rename = "updatedAt")]
    updated_at: String,
    url: String,
    #[serde(rename = "reviewDecision")]
    review_decision: Option<String>,
    reviews: Vec<GhReview>,
}

#[derive(Deserialize)]
struct GhAuthor {
    login: String,
}

#[derive(Deserialize)]
struct GhReview {
    author: GhAuthor,
    state: String,
}

fn fetch_prs_blocking(project_root: &str) -> Result<Vec<PullRequest>, String> {
    let output = Command::new("gh")
        .args([
            "pr",
            "list",
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            "number,title,headRefName,author,createdAt,updatedAt,url,reviewDecision,reviews",
        ])
        .current_dir(project_root)
        .env("LANG", "en_US.UTF-8")
        .env("LC_ALL", "en_US.UTF-8")
        .output()
        .map_err(|e| format!("Failed to run gh: {}", e))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("gh pr list failed: {}", stderr.trim()));
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let gh_prs: Vec<GhPr> =
        serde_json::from_str(&stdout).map_err(|e| format!("JSON parse error: {}", e))?;

    let now_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;

    let prs = gh_prs
        .into_iter()
        .map(|pr| {
            let coderabbit_reviews: Vec<&GhReview> = pr
                .reviews
                .iter()
                .filter(|r| r.author.login == "coderabbitai")
                .collect();

            let coderabbit_status = if let Some(latest) = coderabbit_reviews.last() {
                match latest.state.as_str() {
                    "APPROVED" => CodeRabbitStatus::Approved,
                    "CHANGES_REQUESTED" => CodeRabbitStatus::ChangesRequested,
                    "COMMENTED" => CodeRabbitStatus::Commented,
                    _ => CodeRabbitStatus::Pending,
                }
            } else {
                CodeRabbitStatus::Pending
            };

            // Timeout alert: PR created > 10min ago and still no CodeRabbit review
            let timeout_alert = if coderabbit_reviews.is_empty() {
                parse_iso_to_ms(&pr.created_at)
                    .map(|created_ms| now_ms.saturating_sub(created_ms) > 10 * 60 * 1000)
                    .unwrap_or(false)
            } else {
                false
            };

            let reviews = pr
                .reviews
                .iter()
                .map(|r| PrReview {
                    author: r.author.login.clone(),
                    state: r.state.clone(),
                })
                .collect();

            PullRequest {
                number: pr.number,
                title: pr.title,
                head_ref_name: pr.head_ref_name,
                author: pr.author.login,
                created_at: pr.created_at,
                updated_at: pr.updated_at,
                url: pr.url,
                review_decision: pr.review_decision,
                coderabbit_status,
                timeout_alert,
                reviews,
            }
        })
        .collect();

    Ok(prs)
}

/// Post a comment on a PR to trigger CodeRabbit review.
pub fn trigger_coderabbit_review_blocking(
    project_root: &str,
    pr_number: u32,
) -> Result<(), String> {
    let output = Command::new("gh")
        .args([
            "pr",
            "comment",
            &pr_number.to_string(),
            "--body",
            "@coderabbitai review",
        ])
        .current_dir(project_root)
        .env("LANG", "en_US.UTF-8")
        .env("LC_ALL", "en_US.UTF-8")
        .output()
        .map_err(|e| format!("Failed to run gh: {}", e))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("gh pr comment failed: {}", stderr.trim()));
    }
    Ok(())
}

/// Squash merge a PR after verifying it is approved and not a draft.
pub fn merge_pr_blocking(project_root: &str, pr_number: u32) -> Result<(), String> {
    // Gate: verify PR is approved and not a draft before merging
    let check = Command::new("gh")
        .args([
            "pr",
            "view",
            &pr_number.to_string(),
            "--json",
            "reviewDecision,isDraft",
        ])
        .current_dir(project_root)
        .env("LANG", "en_US.UTF-8")
        .env("LC_ALL", "en_US.UTF-8")
        .output()
        .map_err(|e| format!("Failed to check PR state: {}", e))?;

    if check.status.success() {
        let check_json: serde_json::Value =
            serde_json::from_slice(&check.stdout).unwrap_or_default();
        let decision = check_json
            .get("reviewDecision")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let is_draft = check_json
            .get("isDraft")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        if is_draft {
            return Err("Cannot merge: PR is still a draft".to_string());
        }
        if decision != "APPROVED" {
            return Err(format!(
                "Cannot merge: review decision is '{}', expected 'APPROVED'",
                decision
            ));
        }
    }

    let output = Command::new("gh")
        .args([
            "pr",
            "merge",
            &pr_number.to_string(),
            "--squash",
            "--delete-branch",
        ])
        .current_dir(project_root)
        .env("LANG", "en_US.UTF-8")
        .env("LC_ALL", "en_US.UTF-8")
        .output()
        .map_err(|e| format!("Failed to run gh: {}", e))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("gh pr merge failed: {}", stderr.trim()));
    }
    Ok(())
}

// ─── Utility ───

/// Return current time as Unix milliseconds string (JS-compatible).
fn now_unix_ms() -> String {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .to_string()
}

/// Parse ISO 8601 UTC timestamp to Unix milliseconds.
/// Handles GitHub's format: "2026-03-23T05:00:36Z" or "2026-03-23T05:00:36+00:00".
fn parse_iso_to_ms(iso: &str) -> Option<u64> {
    let iso = iso.trim();
    let parts: Vec<&str> = iso.split('T').collect();
    if parts.len() != 2 {
        return None;
    }
    let date_parts: Vec<u64> = parts[0].split('-').filter_map(|s| s.parse().ok()).collect();
    let time_str = parts[1].trim_end_matches('Z').trim_end_matches("+00:00");
    let time_parts: Vec<u64> = time_str.split(':').filter_map(|s| s.parse().ok()).collect();

    if date_parts.len() != 3 || time_parts.len() < 2 {
        return None;
    }

    let (year, month, day) = (date_parts[0], date_parts[1], date_parts[2]);
    let (hour, min) = (time_parts[0], time_parts[1]);
    let sec = time_parts.get(2).copied().unwrap_or(0);

    // Cumulative days to start of each month (non-leap year)
    const MONTH_DAYS: [u64; 12] = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
    if month < 1 || month > 12 {
        return None;
    }

    // Days from epoch (1970-01-01) to start of `year`
    let years_since = year - 1970;
    let leap_years = (year - 1) / 4 - (year - 1) / 100 + (year - 1) / 400
        - (1969 / 4 - 1969 / 100 + 1969 / 400);
    let days_to_year = years_since * 365 + leap_years;

    // Days within the year
    let is_leap = (year % 4 == 0 && year % 100 != 0) || (year % 400 == 0);
    let leap_day = if is_leap && month > 2 { 1 } else { 0 };
    let day_of_year = MONTH_DAYS[(month - 1) as usize] + leap_day + (day - 1);

    let total_secs = (days_to_year + day_of_year) * 86400 + hour * 3600 + min * 60 + sec;
    Some(total_secs * 1000)
}
