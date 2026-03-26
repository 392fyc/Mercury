use std::fs;
use std::path::PathBuf;
use std::process::Command;
use tauri::State;

use crate::sidecar::SidecarManager;
use crate::{ProjectRoot, SharedPrMonitor, SharedRemoteControl, SharedSidecar};

/// Run a blocking Command in a spawned thread to avoid blocking the tokio runtime.
async fn run_git_command(
    cmd_fn: impl FnOnce() -> std::io::Result<std::process::Output> + Send + 'static,
) -> Result<std::process::Output, String> {
    tokio::task::spawn_blocking(cmd_fn)
        .await
        .map_err(|e| format!("Task join error: {}", e))?
        .map_err(|e| format!("git command failed: {}", e))
}

// ─── Project Info (direct, no sidecar) ───

#[tauri::command]
pub async fn get_project_info(root: State<'_, ProjectRoot>) -> Result<serde_json::Value, String> {
    let project_root = root.0.clone();

    // Detect git branch via `git rev-parse --abbrev-ref HEAD`
    let output = run_git_command(move || {
        Command::new("git")
            .args(["rev-parse", "--abbrev-ref", "HEAD"])
            .current_dir(&project_root)
            .output()
    })
    .await;

    let git_branch = output.ok().and_then(|o| {
        if o.status.success() {
            Some(String::from_utf8_lossy(&o.stdout).trim().to_string())
        } else {
            None
        }
    });

    let project_root = root.0.clone();
    Ok(serde_json::json!({
        "projectRoot": project_root,
        "gitBranch": git_branch,
    }))
}

/// Get git branch for an arbitrary directory (no sidecar needed).
#[tauri::command]
pub async fn get_git_info(path: String) -> Result<serde_json::Value, String> {
    let path_clone = path.clone();
    let output = run_git_command(move || {
        Command::new("git")
            .args(["rev-parse", "--abbrev-ref", "HEAD"])
            .current_dir(&path_clone)
            .output()
    })
    .await;

    let git_branch = output.ok().and_then(|o| {
        if o.status.success() {
            Some(String::from_utf8_lossy(&o.stdout).trim().to_string())
        } else {
            None
        }
    });

    Ok(serde_json::json!({
        "path": path,
        "gitBranch": git_branch,
    }))
}

/// Get git file status (modified/untracked/deleted) for all files in a directory.
///
/// Returns a map of `{ "relative/path": "M"|"D"|"U" }` where:
/// - **M** (Modified): staged (X=M/A) or unstaged (Y=M) modifications
/// - **D** (Deleted): staged (X=D) or unstaged (Y=D) deletions
/// - **U** (Untracked): non-ignored untracked files (XY=??)
///
/// Priority on conflict: M > D > U.
///
/// Uses `git status --porcelain=v1` (single call) for efficiency.
/// Refs: https://git-scm.com/docs/git-status#_output_format
#[tauri::command]
pub async fn get_git_file_status(path: String) -> Result<serde_json::Value, String> {
    // Canonicalize path to prevent symlink/traversal attacks
    let canonical = std::fs::canonicalize(&path)
        .map_err(|e| format!("Invalid path '{}': {}", path, e))?;
    let dir = canonical.to_string_lossy().to_string();

    // Single call: git status --porcelain=v1
    // Output format: "XY filename" where X=staged status, Y=worktree status
    let output = run_git_command(move || {
        Command::new("git")
            .args(["status", "--porcelain=v1"])
            .current_dir(&dir)
            .output()
    })
    .await?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("git status failed: {}", stderr.trim()));
    }

    let mut statuses = serde_json::Map::new();

    for line in String::from_utf8_lossy(&output.stdout).lines() {
            // Each line is at least 3 chars: "XY filename"
            if line.len() < 3 {
                continue;
            }
            let xy: Vec<char> = line.chars().take(2).collect();
            let (x, y) = (xy[0], xy[1]);
            let filename = line[3..].trim();
            if filename.is_empty() {
                continue;
            }

            let status = if x == '?' && y == '?' {
                // Untracked file
                "U"
            } else if x == 'M' || x == 'A' {
                // Staged modification or addition → M
                "M"
            } else if y == 'M' {
                // Unstaged modification → M
                "M"
            } else if x == 'D' {
                // Staged deletion → D
                "D"
            } else if y == 'D' {
                // Unstaged deletion → D
                "D"
            } else {
                // Other statuses (R, C, etc.) treated as M
                "M"
            };

            // Apply priority: M > D > U — only insert if not already a higher-priority status
            let existing = statuses.get(filename).and_then(|v| v.as_str()).unwrap_or("");
            let should_insert = match existing {
                "M" => false,               // M already set; nothing beats M
                "D" => status == "M",       // D already set; only M can overwrite
                _ => true,                  // U or absent; anything overwrites
            };
            if should_insert {
                statuses.insert(filename.to_string(), serde_json::Value::String(status.to_string()));
            }
        }

    Ok(serde_json::Value::Object(statuses))
}

/// Get git diff for a specific file.
///
/// First tries unstaged diff (`git diff`). If the command succeeds but returns
/// empty output (no unstaged changes), falls back to staged diff (`git diff --cached`).
/// If the command fails (non-zero exit), returns an error with stderr.
///
/// Uses `--ignore-cr-at-eol` (Git v2.16+) to suppress CRLF noise on Windows.
/// Ref: https://git-scm.com/docs/git-diff
#[tauri::command]
pub async fn get_git_diff(repo_path: String, file_path: String) -> Result<String, String> {
    let repo_clone = repo_path.clone();
    let file_clone = file_path.clone();
    let output = run_git_command(move || {
        Command::new("git")
            .args(["diff", "--ignore-cr-at-eol", "--", &file_clone])
            .current_dir(&repo_clone)
            .output()
    })
    .await
    .map_err(|e| format!("git diff failed to spawn: {}", e))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("git diff failed: {}", stderr.trim()));
    }

    let unstaged = String::from_utf8_lossy(&output.stdout).to_string();
    if !unstaged.is_empty() {
        return Ok(unstaged);
    }

    // No unstaged changes — try staged diff
    let repo_for_staged = repo_path.clone();
    let file_for_staged = file_path.clone();
    let staged = run_git_command(move || {
        Command::new("git")
            .args(["diff", "--cached", "--ignore-cr-at-eol", "--", &file_for_staged])
            .current_dir(&repo_for_staged)
            .output()
    })
    .await
    .map_err(|e| format!("git diff --cached failed to spawn: {}", e))?;

    if !staged.status.success() {
        let stderr = String::from_utf8_lossy(&staged.stderr);
        return Err(format!("git diff --cached failed: {}", stderr.trim()));
    }

    let staged_diff = String::from_utf8_lossy(&staged.stdout).to_string();
    if !staged_diff.is_empty() {
        return Ok(staged_diff);
    }

    // Both diffs empty — check if this is an untracked (new) file.
    // Use `git diff --no-index <null> <file>` to let Git handle encoding,
    // binary detection, and symlink semantics natively instead of manual assembly.
    #[cfg(windows)]
    let null_path = "NUL";
    #[cfg(not(windows))]
    let null_path = "/dev/null";

    let repo_for_noindex = repo_path.clone();
    let file_for_noindex = file_path.clone();
    let noindex_output = run_git_command(move || {
        Command::new("git")
            .args(["diff", "--no-index", "--ignore-cr-at-eol", "--", null_path, &file_for_noindex])
            .current_dir(&repo_for_noindex)
            .output()
    })
    .await
    .map_err(|e| format!("git diff --no-index failed to spawn: {}", e))?;

    // git diff --no-index exit codes:
    //   0 = no differences (shouldn't happen for new file vs /dev/null)
    //   1 = differences found (expected for a new file)
    //  ≥128 = fatal error (bad args, etc.)
    // Exit code 1 with empty stdout but non-empty stderr indicates a file access
    // error (e.g. permission denied, symlink loop) rather than a real diff.
    let exit_code = noindex_output.status.code().unwrap_or(128);
    let stderr = String::from_utf8_lossy(&noindex_output.stderr);
    let noindex_diff = String::from_utf8_lossy(&noindex_output.stdout).to_string();

    if exit_code >= 128 || (exit_code != 0 && noindex_diff.is_empty() && !stderr.is_empty()) {
        return Err(format!("git diff --no-index failed: {}", stderr.trim()));
    }

    if !noindex_diff.is_empty() {
        return Ok(noindex_diff);
    }

    // Truly no changes
    Ok(String::new())
}

/// List all local and remote git branches for a given directory.
#[tauri::command]
pub async fn list_git_branches(path: String) -> Result<serde_json::Value, String> {
    // Current branch
    let path_clone = path.clone();
    let current_output = run_git_command(move || {
        Command::new("git")
            .args(["rev-parse", "--abbrev-ref", "HEAD"])
            .current_dir(&path_clone)
            .output()
    })
    .await;
    let current = current_output
        .ok()
        .and_then(|o| {
            if o.status.success() {
                Some(String::from_utf8_lossy(&o.stdout).trim().to_string())
            } else {
                None
            }
        })
        .unwrap_or_default();

    // Local branches
    let path_clone = path.clone();
    let local_output = run_git_command(move || {
        Command::new("git")
            .args(["branch", "--format=%(refname:short)"])
            .current_dir(&path_clone)
            .output()
    })
    .await
    .map_err(|e| format!("Failed to list local branches: {}", e))?;
    if !local_output.status.success() {
        let stderr = String::from_utf8_lossy(&local_output.stderr);
        return Err(format!("git branch failed: {}", stderr.trim()));
    }
    let local: Vec<String> = String::from_utf8_lossy(&local_output.stdout)
        .lines()
        .map(|l| l.trim().to_string())
        .filter(|l| !l.is_empty())
        .collect();

    // Remote branches
    let remote_output = run_git_command(move || {
        Command::new("git")
            .args(["branch", "-r", "--format=%(refname:short)"])
            .current_dir(&path)
            .output()
    })
    .await
    .map_err(|e| format!("Failed to list remote branches: {}", e))?;
    if !remote_output.status.success() {
        let stderr = String::from_utf8_lossy(&remote_output.stderr);
        return Err(format!("git branch -r failed: {}", stderr.trim()));
    }
    let remote: Vec<String> = String::from_utf8_lossy(&remote_output.stdout)
        .lines()
        .map(|l| l.trim().to_string())
        .filter(|l| !l.is_empty() && !l.ends_with("/HEAD"))
        .collect();

    Ok(serde_json::json!({
        "current": current,
        "local": local,
        "remote": remote,
    }))
}

/// Checkout a git branch in the specified directory.
/// Validates branch name format before executing to prevent injection.
#[tauri::command]
pub async fn checkout_branch(path: String, branch: String) -> Result<serde_json::Value, String> {
    // Basic branch name validation: reject empty, whitespace, or shell metacharacters
    if branch.is_empty()
        || branch.contains(|c: char| c.is_whitespace() || ";|&$`".contains(c))
        || branch.starts_with('-')
    {
        return Err(format!("Invalid branch name: {}", branch));
    }
    let branch_name = branch.clone();
    let output = run_git_command(move || {
        Command::new("git")
            .args(["checkout", &branch])
            .current_dir(&path)
            .output()
    })
    .await
    .map_err(|e| format!("Failed to checkout branch: {}", e))?;

    if output.status.success() {
        Ok(serde_json::json!({ "ok": true, "branch": branch_name }))
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        Err(format!("git checkout failed: {}", stderr))
    }
}

/// Clone the sidecar manager out of the shared state, or error if not ready.
async fn get_sidecar(shared: &SharedSidecar) -> Result<SidecarManager, String> {
    let guard = shared.lock().await;
    guard
        .as_ref()
        .cloned()
        .ok_or_else(|| "Orchestrator sidecar is still starting".to_string())
}

#[tauri::command]
pub async fn get_agents(sidecar: State<'_, SharedSidecar>) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("get_agents", serde_json::json!({})).await
}

#[tauri::command]
pub async fn send_prompt(
    sidecar: State<'_, SharedSidecar>,
    agent_id: String,
    prompt: String,
    images: Option<serde_json::Value>,
    role: Option<String>,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "send_prompt",
        serde_json::json!({ "agentId": agent_id, "prompt": prompt, "images": images, "role": role }),
    )
    .await
}

#[tauri::command]
pub async fn start_session(
    sidecar: State<'_, SharedSidecar>,
    agent_id: String,
    role: Option<String>,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    let mut params = serde_json::json!({ "agentId": agent_id });
    if let Some(r) = role {
        params["role"] = serde_json::Value::String(r);
    }
    mgr.send_request("start_session", params).await
}

#[tauri::command]
pub async fn stop_session(
    sidecar: State<'_, SharedSidecar>,
    agent_id: String,
    session_id: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "stop_session",
        serde_json::json!({ "agentId": agent_id, "sessionId": session_id }),
    )
    .await
}

#[tauri::command]
pub async fn delete_session(
    sidecar: State<'_, SharedSidecar>,
    agent_id: String,
    session_id: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "delete_session",
        serde_json::json!({ "agentId": agent_id, "sessionId": session_id }),
    )
    .await
}

#[tauri::command]
pub async fn configure_agent(
    sidecar: State<'_, SharedSidecar>,
    config: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("configure_agent", serde_json::json!({ "config": config }))
        .await
}

#[tauri::command]
pub async fn dispatch_task(
    sidecar: State<'_, SharedSidecar>,
    params: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("dispatch_task", params).await
}

// ─── Config Commands ───

#[tauri::command]
pub async fn get_config(sidecar: State<'_, SharedSidecar>) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("get_config", serde_json::json!({})).await
}

#[tauri::command]
pub async fn update_config(
    sidecar: State<'_, SharedSidecar>,
    config: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("update_config", serde_json::json!({ "config": config }))
        .await
}

// ─── Task Orchestration Commands ───

#[tauri::command]
pub async fn create_task(
    sidecar: State<'_, SharedSidecar>,
    params: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("create_task", params).await
}

#[tauri::command]
pub async fn get_task(
    sidecar: State<'_, SharedSidecar>,
    task_id: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("get_task", serde_json::json!({ "taskId": task_id }))
        .await
}

#[tauri::command]
pub async fn list_tasks(
    sidecar: State<'_, SharedSidecar>,
    status: Option<String>,
    assigned_to: Option<String>,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "list_tasks",
        serde_json::json!({ "status": status, "assignedTo": assigned_to }),
    )
    .await
}

#[tauri::command]
pub async fn record_receipt(
    sidecar: State<'_, SharedSidecar>,
    task_id: String,
    receipt: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "record_receipt",
        serde_json::json!({ "taskId": task_id, "receipt": receipt }),
    )
    .await
}

#[tauri::command]
pub async fn create_acceptance(
    sidecar: State<'_, SharedSidecar>,
    task_id: String,
    acceptor_id: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "create_acceptance",
        serde_json::json!({ "taskId": task_id, "acceptorId": acceptor_id }),
    )
    .await
}

#[tauri::command]
pub async fn record_acceptance_result(
    sidecar: State<'_, SharedSidecar>,
    acceptance_id: String,
    results: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "record_acceptance_result",
        serde_json::json!({ "acceptanceId": acceptance_id, "results": results }),
    )
    .await
}

#[tauri::command]
pub async fn create_issue(
    sidecar: State<'_, SharedSidecar>,
    params: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("create_issue", params).await
}

#[tauri::command]
pub async fn resolve_issue(
    sidecar: State<'_, SharedSidecar>,
    issue_id: String,
    resolution: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "resolve_issue",
        serde_json::json!({ "issueId": issue_id, "resolution": resolution }),
    )
    .await
}

#[tauri::command]
pub async fn summarize_session(
    sidecar: State<'_, SharedSidecar>,
    agent_id: String,
    summary: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "summarize_session",
        serde_json::json!({ "agentId": agent_id, "summary": summary }),
    )
    .await
}

// ─── Slash Commands ───

#[tauri::command]
pub async fn get_slash_commands(
    sidecar: State<'_, SharedSidecar>,
    agent_id: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "get_slash_commands",
        serde_json::json!({ "agentId": agent_id }),
    )
    .await
}

// ─── Model Listing & Switching ───

#[tauri::command]
pub async fn list_models(
    sidecar: State<'_, SharedSidecar>,
    agent_id: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "list_models",
        serde_json::json!({ "agentId": agent_id }),
    )
    .await
}

#[tauri::command]
pub async fn set_model(
    sidecar: State<'_, SharedSidecar>,
    agent_id: String,
    model: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "set_model",
        serde_json::json!({ "agentId": agent_id, "model": model }),
    )
    .await
}

// ─── Knowledge Base Commands (optional) ───

#[tauri::command]
pub async fn kb_read(
    sidecar: State<'_, SharedSidecar>,
    file: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("kb_read", serde_json::json!({ "file": file }))
        .await
}

#[tauri::command]
pub async fn kb_search(
    sidecar: State<'_, SharedSidecar>,
    query: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("kb_search", serde_json::json!({ "query": query }))
        .await
}

#[tauri::command]
pub async fn kb_list(
    sidecar: State<'_, SharedSidecar>,
    folder: Option<String>,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("kb_list", serde_json::json!({ "folder": folder }))
        .await
}

#[tauri::command]
pub async fn kb_write(
    sidecar: State<'_, SharedSidecar>,
    name: String,
    content: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "kb_write",
        serde_json::json!({ "name": name, "content": content }),
    )
    .await
}

#[tauri::command]
pub async fn kb_append(
    sidecar: State<'_, SharedSidecar>,
    file: String,
    content: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "kb_append",
        serde_json::json!({ "file": file, "content": content }),
    )
    .await
}

// ─── Agent Workspace Commands ───

#[tauri::command]
pub async fn set_agent_cwd(
    sidecar: State<'_, SharedSidecar>,
    agent_id: String,
    cwd: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "set_agent_cwd",
        serde_json::json!({ "agentId": agent_id, "cwd": cwd }),
    )
    .await
}

// ─── Session Resume Commands ───

#[tauri::command]
pub async fn list_sessions(
    sidecar: State<'_, SharedSidecar>,
    agent_id: Option<String>,
    role: Option<String>,
    include_terminal: Option<bool>,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "list_sessions",
        serde_json::json!({
            "agentId": agent_id,
            "role": role,
            "includeTerminal": include_terminal
        }),
    )
        .await
}

#[tauri::command]
pub async fn resume_session(
    sidecar: State<'_, SharedSidecar>,
    agent_id: String,
    session_id: String,
    expected_role: Option<String>,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "resume_session",
        serde_json::json!({
            "agentId": agent_id,
            "sessionId": session_id,
            "expectedRole": expected_role
        }),
    )
    .await
}

#[tauri::command]
pub async fn get_session_messages(
    sidecar: State<'_, SharedSidecar>,
    session_id: String,
    offset: Option<usize>,
    limit: Option<usize>,
    agent_id: Option<String>,
    role: Option<String>,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "get_session_messages",
        serde_json::json!({
            "sessionId": session_id,
            "offset": offset,
            "limit": limit,
            "agentId": agent_id,
            "role": role
        }),
    )
    .await
}

// ─── Approval Control Plane Commands ───

#[tauri::command]
pub async fn get_approval_mode(
    sidecar: State<'_, SharedSidecar>,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("get_approval_mode", serde_json::json!({}))
        .await
}

#[tauri::command]
pub async fn set_approval_mode(
    sidecar: State<'_, SharedSidecar>,
    mode: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("set_approval_mode", serde_json::json!({ "mode": mode }))
        .await
}

#[tauri::command]
pub async fn list_approval_requests(
    sidecar: State<'_, SharedSidecar>,
    status: Option<String>,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "list_approval_requests",
        serde_json::json!({ "status": status }),
    )
    .await
}

#[tauri::command]
pub async fn approve_request(
    sidecar: State<'_, SharedSidecar>,
    request_id: String,
    reason: Option<String>,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "approve_request",
        serde_json::json!({ "requestId": request_id, "reason": reason }),
    )
    .await
}

#[tauri::command]
pub async fn deny_request(
    sidecar: State<'_, SharedSidecar>,
    request_id: String,
    reason: Option<String>,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "deny_request",
        serde_json::json!({ "requestId": request_id, "reason": reason }),
    )
    .await
}

// ─── Shared Context Commands ───

#[tauri::command]
pub async fn refresh_context(
    sidecar: State<'_, SharedSidecar>,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("refresh_context", serde_json::json!({}))
        .await
}

#[tauri::command]
pub async fn get_context_status(
    sidecar: State<'_, SharedSidecar>,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("get_context_status", serde_json::json!({}))
        .await
}

#[tauri::command]
pub async fn get_role_instructions(
    sidecar: State<'_, SharedSidecar>,
    role: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("get_role_instructions", serde_json::json!({ "role": role }))
        .await
}

// ─── Session History Commands (direct filesystem, no sidecar) ───

/// Read native session history from JSONL files on disk.
#[tauri::command]
pub async fn read_session_history(
    root: State<'_, ProjectRoot>,
    cli_type: String,
    session_id: String,
    cwd: Option<String>,
) -> Result<serde_json::Value, String> {
    // Validate session_id to prevent path traversal attacks
    if session_id.contains("..")
        || session_id.contains('/')
        || session_id.contains('\\')
        || session_id.contains('\0')
        || session_id.is_empty()
    {
        return Err(format!("Invalid session ID: {}", session_id));
    }

    let effective_cwd = cwd.unwrap_or_else(|| root.0.clone());
    let jsonl_path = match cli_type.as_str() {
        "claude" => resolve_claude_session_path(&effective_cwd, &session_id),
        "codex" => resolve_codex_session_path(&session_id),
        _ => return Err(format!("Unknown CLI type: {}", cli_type)),
    };

    let path = jsonl_path?;
    if !path.exists() {
        return Ok(serde_json::json!({ "messages": [], "source": path.display().to_string() }));
    }

    // Read the file in a blocking thread to avoid stalling the tokio runtime
    let (content, source) = tokio::task::spawn_blocking(move || {
        let source = path.display().to_string();
        let content = fs::read_to_string(&path)
            .map_err(|e| format!("Failed to read session file: {}", e))?;
        Ok::<_, String>((content, source))
    })
    .await
    .map_err(|e| format!("Task join error: {}", e))??;

    let mut messages = Vec::new();
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Ok(obj) = serde_json::from_str::<serde_json::Value>(line) {
            if let Some(msg) = extract_message_from_jsonl(&obj, &cli_type) {
                messages.push(msg);
            }
        }
    }

    Ok(serde_json::json!({
        "messages": messages,
        "source": source,
        "total": messages.len(),
    }))
}

/// Resolve Claude Code session JSONL path.
/// Claude encodes the cwd in the path: ~/.claude/projects/<hex-encoded-cwd>/<sessionId>.jsonl
fn resolve_claude_session_path(cwd: &str, session_id: &str) -> Result<PathBuf, String> {
    let home = dirs::home_dir().ok_or("Cannot determine home directory")?;
    // Claude Code encodes the cwd as a hex string for the directory name
    let encoded_cwd = cwd.as_bytes().iter().map(|b| format!("{:02x}", b)).collect::<String>();
    let path = home
        .join(".claude")
        .join("projects")
        .join(&encoded_cwd)
        .join(format!("{}.jsonl", session_id));
    Ok(path)
}

/// Resolve Codex CLI session JSONL path.
/// Codex stores sessions at: ~/.codex/sessions/<threadId>.jsonl
fn resolve_codex_session_path(session_id: &str) -> Result<PathBuf, String> {
    let home = dirs::home_dir().ok_or("Cannot determine home directory")?;
    let path = home
        .join(".codex")
        .join("sessions")
        .join(format!("{}.jsonl", session_id));
    Ok(path)
}

/// Extract a user/assistant message from a JSONL line object.
/// Claude: { "role": "user"|"assistant", "content": "..." }
/// Codex:  { "type": "agentMessage"|"userMessage", "text": "...", "content": [...] }
fn extract_message_from_jsonl(obj: &serde_json::Value, cli_type: &str) -> Option<serde_json::Value> {
    match cli_type {
        "claude" => {
            let role = obj.get("role")?.as_str()?;
            if role != "user" && role != "assistant" {
                return None;
            }
            // Content can be string or array of blocks
            let content = if let Some(s) = obj.get("content").and_then(|v| v.as_str()) {
                s.to_string()
            } else if let Some(arr) = obj.get("content").and_then(|v| v.as_array()) {
                arr.iter()
                    .filter_map(|block| block.get("text").and_then(|t| t.as_str()))
                    .collect::<Vec<_>>()
                    .join("\n")
            } else {
                return None;
            };
            if content.is_empty() {
                return None;
            }
            Some(serde_json::json!({
                "role": role,
                "content": content,
                "timestamp": obj.get("timestamp").and_then(|t| t.as_f64()).unwrap_or(0.0) as u64,
            }))
        }
        "codex" => {
            let item_type = obj.get("type")?.as_str()?;
            let (role, text) = match item_type {
                "userMessage" => {
                    let content = obj.get("content").and_then(|v| v.as_array())?;
                    let text: String = content
                        .iter()
                        .filter_map(|c| c.get("text").and_then(|t| t.as_str()))
                        .collect::<Vec<_>>()
                        .join("\n");
                    ("user", text)
                }
                "agentMessage" => {
                    let text = obj.get("text").and_then(|t| t.as_str())?.to_string();
                    ("assistant", text)
                }
                _ => return None,
            };
            if text.is_empty() {
                return None;
            }
            Some(serde_json::json!({
                "role": role,
                "content": text,
                "timestamp": 0,
            }))
        }
        _ => None,
    }
}

// ─── Remote Control Commands ───

#[tauri::command]
pub async fn start_remote_control(
    app: tauri::AppHandle,
    rc: State<'_, SharedRemoteControl>,
    root: State<'_, ProjectRoot>,
    session_name: Option<String>,
) -> Result<serde_json::Value, String> {
    let mgr = rc.lock().await;
    mgr.start(app, root.0.clone(), session_name).await?;
    Ok(serde_json::json!({ "ok": true }))
}

#[tauri::command]
pub async fn stop_remote_control(
    rc: State<'_, SharedRemoteControl>,
) -> Result<serde_json::Value, String> {
    let mgr = rc.lock().await;
    mgr.stop().await?;
    Ok(serde_json::json!({ "ok": true }))
}

#[tauri::command]
pub async fn get_remote_control_status(
    rc: State<'_, SharedRemoteControl>,
) -> Result<serde_json::Value, String> {
    let mgr = rc.lock().await;
    let state = mgr.get_state().await;
    serde_json::to_value(&state).map_err(|e| e.to_string())
}

// ─── PR Monitor Commands ───

#[tauri::command]
pub async fn get_open_prs(
    pm: State<'_, SharedPrMonitor>,
) -> Result<serde_json::Value, String> {
    let prs = pm.fetch_prs().await?;
    Ok(serde_json::json!({ "prs": prs }))
}

#[tauri::command]
pub async fn get_pr_monitor_state(
    pm: State<'_, SharedPrMonitor>,
) -> Result<serde_json::Value, String> {
    let state = pm.get_state().await;
    serde_json::to_value(&state).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn start_pr_polling(
    app: tauri::AppHandle,
    pm: State<'_, SharedPrMonitor>,
    interval_secs: Option<u64>,
) -> Result<serde_json::Value, String> {
    pm.start_polling(app, interval_secs).await?;
    Ok(serde_json::json!({ "ok": true }))
}

#[tauri::command]
pub async fn stop_pr_polling(
    pm: State<'_, SharedPrMonitor>,
) -> Result<serde_json::Value, String> {
    pm.stop_polling();
    Ok(serde_json::json!({ "ok": true }))
}

#[tauri::command]
pub async fn trigger_coderabbit_review(
    root: State<'_, ProjectRoot>,
    pr_number: u32,
) -> Result<serde_json::Value, String> {
    if pr_number == 0 {
        return Err("Invalid PR number".to_string());
    }
    let project_root = root.0.clone();
    tokio::task::spawn_blocking(move || {
        crate::pr_monitor::trigger_coderabbit_review_blocking(&project_root, pr_number)
    })
    .await
    .map_err(|e| format!("Task join error: {}", e))??;
    Ok(serde_json::json!({ "ok": true }))
}

#[tauri::command]
pub async fn merge_pr(
    root: State<'_, ProjectRoot>,
    pr_number: u32,
) -> Result<serde_json::Value, String> {
    if pr_number == 0 {
        return Err("Invalid PR number".to_string());
    }
    let project_root = root.0.clone();
    tokio::task::spawn_blocking(move || {
        crate::pr_monitor::merge_pr_blocking(&project_root, pr_number)
    })
    .await
    .map_err(|e| format!("Task join error: {}", e))??;
    Ok(serde_json::json!({ "ok": true }))
}
