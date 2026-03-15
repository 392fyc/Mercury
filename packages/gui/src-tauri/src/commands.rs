use tauri::State;

use crate::sidecar::SidecarManager;
use crate::SharedSidecar;

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
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "send_prompt",
        serde_json::json!({ "agentId": agent_id, "prompt": prompt }),
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
    from_agent_id: String,
    to_agent_id: String,
    prompt: String,
) -> Result<serde_json::Value, String> {
    let mgr = get_sidecar(&sidecar).await?;
    mgr.send_request(
        "dispatch_task",
        serde_json::json!({
            "fromAgentId": from_agent_id,
            "toAgentId": to_agent_id,
            "prompt": prompt,
        }),
    )
    .await
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
