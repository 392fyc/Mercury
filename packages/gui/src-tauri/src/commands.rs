use std::fs;
use std::path::PathBuf;
use std::process::Command;
use tauri::State;

use crate::sidecar::SidecarManager;
use crate::{ProjectRoot, SharedSidecar};

// ─── Project Info (direct, no sidecar) ───

#[tauri::command]
pub fn get_project_info(root: State<'_, ProjectRoot>) -> Result<serde_json::Value, String> {
    let project_root = &root.0;

    // Detect git branch via `git rev-parse --abbrev-ref HEAD`
    let git_branch = Command::new("git")
        .args(["rev-parse", "--abbrev-ref", "HEAD"])
        .current_dir(project_root)
        .output()
        .ok()
        .and_then(|o| {
            if o.status.success() {
                Some(String::from_utf8_lossy(&o.stdout).trim().to_string())
            } else {
                None
            }
        });

    Ok(serde_json::json!({
        "projectRoot": project_root,
        "gitBranch": git_branch,
    }))
}

/// Get git branch for an arbitrary directory (no sidecar needed).
#[tauri::command]
pub fn get_git_info(path: String) -> Result<serde_json::Value, String> {
    let git_branch = Command::new("git")
        .args(["rev-parse", "--abbrev-ref", "HEAD"])
        .current_dir(&path)
        .output()
        .ok()
        .and_then(|o| {
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

/// List all local and remote git branches for a given directory.
#[tauri::command]
pub fn list_git_branches(path: String) -> Result<serde_json::Value, String> {
    // Current branch
    let current = Command::new("git")
        .args(["rev-parse", "--abbrev-ref", "HEAD"])
        .current_dir(&path)
        .output()
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
    let local_output = Command::new("git")
        .args(["branch", "--format=%(refname:short)"])
        .current_dir(&path)
        .output()
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
    let remote_output = Command::new("git")
        .args(["branch", "-r", "--format=%(refname:short)"])
        .current_dir(&path)
        .output()
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
pub fn checkout_branch(path: String, branch: String) -> Result<serde_json::Value, String> {
    // Basic branch name validation: reject empty, whitespace, or shell metacharacters
    if branch.is_empty()
        || branch.contains(|c: char| c.is_whitespace() || ";|&$`".contains(c))
        || branch.starts_with('-')
    {
        return Err(format!("Invalid branch name: {}", branch));
    }
    let output = Command::new("git")
        .args(["checkout", &branch])
        .current_dir(&path)
        .output()
        .map_err(|e| format!("Failed to checkout branch: {}", e))?;

    if output.status.success() {
        Ok(serde_json::json!({ "ok": true, "branch": branch }))
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
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "get_session_messages",
        serde_json::json!({
            "sessionId": session_id,
            "offset": offset,
            "limit": limit
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

// ─── Session History Commands (direct filesystem, no sidecar) ───

/// Read native CLI session history from JSONL files.
///
/// Claude Code: ~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl
/// Codex CLI:   ~/.codex/sessions/<threadId>.jsonl (via app-server)
///
/// Returns parsed messages from the JSONL file for history backfill on resume.
#[tauri::command]
pub fn read_session_history(
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

    let content = fs::read_to_string(&path)
        .map_err(|e| format!("Failed to read session file: {}", e))?;

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
        "source": path.display().to_string(),
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
