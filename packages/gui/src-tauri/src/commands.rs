use std::process::Command;
use tauri::State;

use crate::sidecar::SidecarManager;
use crate::{ProjectRoot, SharedSidecar};

// ─── Project Info (direct, no sidecar) ───

#[tauri::command]
pub fn get_project_info(
    root: State<'_, ProjectRoot>,
) -> Result<serde_json::Value, String> {
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

/// Clone the sidecar manager out of the shared state, or error if not ready.
async fn get_sidecar(shared: &SharedSidecar) -> Result<SidecarManager, String> {
    let guard = shared.lock().await;
    guard
        .as_ref()
        .cloned()
        .ok_or_else(|| "Orchestrator sidecar is still starting".to_string())
}

#[tauri::command]
pub async fn get_agents(
    sidecar: State<'_, SharedSidecar>,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("get_agents", serde_json::json!({})).await
}

#[tauri::command]
pub async fn send_prompt(
    sidecar: State<'_, SharedSidecar>,
    agent_id: String,
    prompt: String,
    images: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "send_prompt",
        serde_json::json!({ "agentId": agent_id, "prompt": prompt, "images": images }),
    )
    .await
}

#[tauri::command]
pub async fn start_session(
    sidecar: State<'_, SharedSidecar>,
    agent_id: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "start_session",
        serde_json::json!({ "agentId": agent_id }),
    )
    .await
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
pub async fn get_config(
    sidecar: State<'_, SharedSidecar>,
) -> Result<serde_json::Value, String> {
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
    mgr.send_request("list_tasks", serde_json::json!({ "status": status, "assignedTo": assigned_to }))
        .await
}

#[tauri::command]
pub async fn record_receipt(
    sidecar: State<'_, SharedSidecar>,
    task_id: String,
    receipt: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("record_receipt", serde_json::json!({ "taskId": task_id, "receipt": receipt }))
        .await
}

#[tauri::command]
pub async fn create_acceptance(
    sidecar: State<'_, SharedSidecar>,
    task_id: String,
    acceptor_id: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("create_acceptance", serde_json::json!({ "taskId": task_id, "acceptorId": acceptor_id }))
        .await
}

#[tauri::command]
pub async fn record_acceptance_result(
    sidecar: State<'_, SharedSidecar>,
    acceptance_id: String,
    results: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("record_acceptance_result", serde_json::json!({ "acceptanceId": acceptance_id, "results": results }))
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
    mgr.send_request("resolve_issue", serde_json::json!({ "issueId": issue_id, "resolution": resolution }))
        .await
}

#[tauri::command]
pub async fn summarize_session(
    sidecar: State<'_, SharedSidecar>,
    agent_id: String,
    summary: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("summarize_session", serde_json::json!({ "agentId": agent_id, "summary": summary }))
        .await
}

// ─── Slash Commands ───

#[tauri::command]
pub async fn get_slash_commands(
    sidecar: State<'_, SharedSidecar>,
    agent_id: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("get_slash_commands", serde_json::json!({ "agentId": agent_id }))
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
    mgr.send_request("kb_write", serde_json::json!({ "name": name, "content": content }))
        .await
}

#[tauri::command]
pub async fn kb_append(
    sidecar: State<'_, SharedSidecar>,
    file: String,
    content: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("kb_append", serde_json::json!({ "file": file, "content": content }))
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
    mgr.send_request("set_agent_cwd", serde_json::json!({ "agentId": agent_id, "cwd": cwd }))
        .await
}

// ─── Shared Context Commands ───

#[tauri::command]
pub async fn refresh_context(
    sidecar: State<'_, SharedSidecar>,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("refresh_context", serde_json::json!({})).await
}

#[tauri::command]
pub async fn get_context_status(
    sidecar: State<'_, SharedSidecar>,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request("get_context_status", serde_json::json!({})).await
}
