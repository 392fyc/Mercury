mod commands;
mod sidecar;
mod types;

use tauri::{Emitter, Manager};
use sidecar::SidecarManager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let app_handle = app.handle().clone();

            // Get the project directory (where mercury.config.json lives)
            let project_dir = std::env::current_dir()
                .map(|p| p.to_string_lossy().to_string())
                .unwrap_or_else(|_| ".".to_string());

            // Spawn the Node.js orchestrator sidecar
            tauri::async_runtime::spawn(async move {
                match SidecarManager::spawn(app_handle.clone(), project_dir).await {
                    Ok(manager) => {
                        eprintln!("[tauri] Orchestrator sidecar started");
                        app_handle.manage(manager);
                    }
                    Err(e) => {
                        eprintln!("[tauri] Failed to start orchestrator: {}", e);
                        // Emit error event so the frontend knows
                        let _ = app_handle.emit(
                            "sidecar-error",
                            serde_json::json!({ "error": e }),
                        );
                    }
                }
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_agents,
            commands::send_prompt,
            commands::start_session,
            commands::stop_session,
            commands::configure_agent,
            commands::dispatch_task,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
