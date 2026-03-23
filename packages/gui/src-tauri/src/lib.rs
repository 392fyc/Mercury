mod commands;
mod pr_monitor;
mod remote_control;
mod sidecar;
mod types;

use std::path::PathBuf;
use std::sync::Arc;
use tauri::{Emitter, Manager};
use tokio::sync::Mutex;

use pr_monitor::PrMonitor;
use remote_control::RemoteControlManager;
use sidecar::SidecarManager;

/// Shared sidecar handle — `None` until the sidecar finishes spawning.
pub type SharedSidecar = Arc<Mutex<Option<SidecarManager>>>;

/// Shared remote control manager.
pub type SharedRemoteControl = Arc<Mutex<RemoteControlManager>>;

/// Shared PR monitor — no outer Mutex needed; PrMonitor has internal fine-grained locking.
pub type SharedPrMonitor = Arc<PrMonitor>;

/// Project root path — available immediately (no sidecar dependency).
pub struct ProjectRoot(pub String);

/// Walk up from `start` to find the monorepo root that contains the orchestrator entrypoint.
fn find_project_root(start: PathBuf) -> Option<PathBuf> {
    let mut dir = start;
    loop {
        if dir.join("pnpm-workspace.yaml").exists()
            && dir.join("package.json").exists()
            && dir.join("packages").join("orchestrator").join("src").join("index.ts").exists()
        {
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
        // Single-instance is disabled during `tauri dev` (debug_assertions enabled)
        // because the dev runner may spawn multiple cargo processes, causing the
        // second instance to exit and kill the dev pipeline.
        // In release builds, the second instance notifies the first and exits with code 0.
        tauri::Builder::default().plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            // Restore and focus the existing window regardless of its current state
            // (minimized, hidden, or normal). Order matters: show → unminimize → focus.
            if let Some(w) = app.get_webview_window("main") {
                if let Err(e) = w.show() { eprintln!("[single-instance] show failed: {e}"); }
                if let Err(e) = w.unminimize() { eprintln!("[single-instance] unminimize failed: {e}"); }
                if let Err(e) = w.set_focus() { eprintln!("[single-instance] set_focus failed: {e}"); }
            } else {
                eprintln!("[single-instance] main window not found");
            }
        }))
    };

    #[cfg(debug_assertions)]
    let builder = tauri::Builder::default();

    builder
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .setup(|app| {
            let app_handle = app.handle().clone();

            // Register shared sidecar state synchronously (avoids race condition)
            let shared: SharedSidecar = Arc::new(Mutex::new(None));
            app.manage(shared.clone());

            // Register remote control manager
            let rc: SharedRemoteControl = Arc::new(Mutex::new(RemoteControlManager::new()));
            app.manage(rc.clone());

            // Resolve the monorepo root (where mercury.config.json lives)
            let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
            let cwd = std::env::current_dir().unwrap_or_else(|_| manifest_dir.clone());
            let project_dir = find_project_root(cwd)
                .or_else(|| find_project_root(manifest_dir.clone()))
                .unwrap_or(manifest_dir)
                .to_string_lossy()
                .to_string();

            app.manage(ProjectRoot(project_dir.clone()));
            eprintln!("[tauri] Project root: {}", project_dir);

            // Register PR monitor with project root available immediately
            let pm: SharedPrMonitor = Arc::new(PrMonitor::new(project_dir.clone()));
            app.manage(pm.clone());

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
            commands::list_git_branches,
            commands::checkout_branch,
            commands::get_agents,
            commands::send_prompt,
            commands::start_session,
            commands::stop_session,
            commands::delete_session,
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
            commands::list_models,
            commands::set_model,
            commands::read_session_history,
            commands::start_remote_control,
            commands::stop_remote_control,
            commands::get_remote_control_status,
            commands::get_open_prs,
            commands::get_pr_monitor_state,
            commands::start_pr_polling,
            commands::stop_pr_polling,
            commands::trigger_coderabbit_review,
            commands::merge_pr,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                let shared = app.state::<SharedSidecar>().inner().clone();
                let rc = app.state::<SharedRemoteControl>().inner().clone();
                let pm = app.state::<SharedPrMonitor>().inner().clone();
                tauri::async_runtime::block_on(async {
                    // Stop PR monitor polling
                    if pm.is_polling() {
                        eprintln!("[tauri] Stopping PR monitor polling");
                        pm.stop_polling();
                    }
                    // Shutdown remote control first
                    {
                        let mgr = rc.lock().await;
                        if mgr.is_running().await {
                            eprintln!("[tauri] Shutting down remote control");
                            let _ = mgr.stop().await;
                        }
                    }
                    // Then shutdown sidecar
                    let guard = shared.lock().await;
                    if let Some(mgr) = guard.as_ref() {
                        eprintln!("[tauri] Shutting down orchestrator sidecar");
                        mgr.shutdown().await;
                    }
                });
            }
        });
}
