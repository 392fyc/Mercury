use tauri::State;

use crate::sidecar::SidecarManager;

#[tauri::command]
pub async fn get_agents(
    sidecar: State<'_, SidecarManager>,
) -> Result<serde_json::Value, String> {
    sidecar
        .send_request("get_agents", serde_json::json!({}))
        .await
}

#[tauri::command]
pub async fn send_prompt(
    sidecar: State<'_, SidecarManager>,
    agent_id: String,
    prompt: String,
) -> Result<serde_json::Value, String> {
    sidecar
        .send_request(
            "send_prompt",
            serde_json::json!({ "agentId": agent_id, "prompt": prompt }),
        )
        .await
}

#[tauri::command]
pub async fn start_session(
    sidecar: State<'_, SidecarManager>,
    agent_id: String,
) -> Result<serde_json::Value, String> {
    sidecar
        .send_request(
            "start_session",
            serde_json::json!({ "agentId": agent_id }),
        )
        .await
}

#[tauri::command]
pub async fn stop_session(
    sidecar: State<'_, SidecarManager>,
    agent_id: String,
    session_id: String,
) -> Result<serde_json::Value, String> {
    sidecar
        .send_request(
            "stop_session",
            serde_json::json!({ "agentId": agent_id, "sessionId": session_id }),
        )
        .await
}

#[tauri::command]
pub async fn configure_agent(
    sidecar: State<'_, SidecarManager>,
    config: serde_json::Value,
) -> Result<serde_json::Value, String> {
    sidecar
        .send_request("configure_agent", serde_json::json!({ "config": config }))
        .await
}

#[tauri::command]
pub async fn dispatch_task(
    sidecar: State<'_, SidecarManager>,
    from_agent_id: String,
    to_agent_id: String,
    prompt: String,
) -> Result<serde_json::Value, String> {
    sidecar
        .send_request(
            "dispatch_task",
            serde_json::json!({
                "fromAgentId": from_agent_id,
                "toAgentId": to_agent_id,
                "prompt": prompt,
            }),
        )
        .await
}
