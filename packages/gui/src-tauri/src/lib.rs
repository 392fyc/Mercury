mod commands;
mod sidecar;
mod types;

use std::path::PathBuf;
use std::sync::Arc;
use tauri::{Emitter, Manager};
use tokio::sync::Mutex;

use sidecar::SidecarManager;

/// Shared sidecar handle — `None` until the sidecar finishes spawning.
pub type SharedSidecar = Arc<Mutex<Option<SidecarManager>>>;

/// Project root path — available immediately (no sidecar dependency).
pub struct ProjectRoot(pub String);

/// Walk up from `start` to find the directory containing `mercury.config.json`.
fn find_project_root(start: PathBuf) -> Option<PathBuf> {
    let mut dir = start;
    loop {
        if dir.join("mercury.config.json").exists() {
            return Some(dir);
        }
        if !dir.pop() {
            return None;
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[cfg(not(debug_assertions))]
    let builder = {
        // Keep single-instance enforcement out of `tauri dev`, which may restart/spawn
        // the app process during development.
        tauri::Builder::default().plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.set_focus();
            }
        }))
    };

    #[cfg(debug_assertions)]
    let builder = tauri::Builder::default();

    builder
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let app_handle = app.handle().clone();

            // Register shared sidecar state synchronously (avoids race condition)
            let shared: SharedSidecar = Arc::new(Mutex::new(None));
            app.manage(shared.clone());

            // Resolve the monorepo root (where mercury.config.json lives)
            let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
            let project_dir = find_project_root(cwd.clone())
                .unwrap_or(cwd)
                .to_string_lossy()
                .to_string();

            app.manage(ProjectRoot(project_dir.clone()));
            eprintln!("[tauri] Project root: {}", project_dir);

            // Spawn the Node.js orchestrator sidecar
            tauri::async_runtime::spawn(async move {
                match SidecarManager::spawn(app_handle.clone(), project_dir).await {
                    Ok(manager) => {
                        eprintln!("[tauri] Orchestrator sidecar started");
                        let mut guard = shared.lock().await;
                        *guard = Some(manager);
                    }
                    Err(e) => {
                        eprintln!("[tauri] Failed to start orchestrator: {}", e);
                        let _ = app_handle.emit("sidecar-error", serde_json::json!({ "error": e }));
                    }
                }
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_project_info,
            commands::get_git_info,
            commands::get_agents,
            commands::send_prompt,
            commands::start_session,
            commands::stop_session,
            commands::configure_agent,
            commands::dispatch_task,
            commands::get_config,
            commands::update_config,
            commands::create_task,
            commands::get_task,
            commands::list_tasks,
            commands::record_receipt,
            commands::create_acceptance,
            commands::record_acceptance_result,
            commands::create_issue,
            commands::resolve_issue,
            commands::summarize_session,
            commands::get_slash_commands,
            commands::kb_read,
            commands::kb_search,
            commands::kb_list,
            commands::kb_write,
            commands::kb_append,
            commands::set_agent_cwd,
            commands::list_sessions,
            commands::resume_session,
            commands::get_session_messages,
            commands::get_approval_mode,
            commands::set_approval_mode,
            commands::list_approval_requests,
            commands::approve_request,
            commands::deny_request,
            commands::refresh_context,
            commands::get_context_status,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                let shared = app.state::<SharedSidecar>().inner().clone();
                tauri::async_runtime::block_on(async {
                    let guard = shared.lock().await;
                    if let Some(mgr) = guard.as_ref() {
                        eprintln!("[tauri] Shutting down orchestrator sidecar");
                        mgr.shutdown().await;
                    }
                });
            }
        });
}
