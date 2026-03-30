use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use tauri::Emitter;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::{oneshot, Mutex};
use tokio::time::{timeout, Duration};

use crate::types::RpcRequest;
#[cfg(target_os = "windows")]
use crate::windows_job::ProcessJob;

#[derive(Clone)]
pub struct SidecarManager {
    stdin: Arc<Mutex<tokio::process::ChildStdin>>,
    child: Arc<Mutex<Child>>,
    #[cfg(target_os = "windows")]
    _job: Option<Arc<ProcessJob>>,
    pending: Arc<Mutex<HashMap<u64, oneshot::Sender<serde_json::Value>>>>,
    next_id: Arc<Mutex<u64>>,
}

impl SidecarManager {
    pub async fn spawn(app_handle: tauri::AppHandle, project_dir: String) -> Result<Self, String> {
        let orchestrator_entry = resolve_orchestrator_entry(&project_dir)?;
        #[cfg(target_os = "windows")]
        let job = match ProcessJob::new_kill_on_close() {
            Ok(job) => Some(Arc::new(job)),
            Err(e) => {
                eprintln!(
                    "[tauri] WARNING: failed to create sidecar job object: {}",
                    e
                );
                None
            }
        };

        // In dev mode, use pnpm exec tsx to run the orchestrator.
        // Using pnpm instead of npx avoids npm warn noise from inherited env vars.
        // On Windows, pnpm is a .cmd script so we must run via cmd.exe.
        #[cfg(target_os = "windows")]
        let mut cmd = {
            let mut c = Command::new("cmd");
            c.args(["/c", "pnpm", "exec", "tsx"]);
            c.arg(&orchestrator_entry);
            // Force UTF-8 encoding to prevent codepage 936/GBK garbling on CJK Windows.
            // PYTHONIOENCODING: protects any Python subprocess spawned downstream.
            c.creation_flags(0x08000000); // CREATE_NO_WINDOW
            c.env("LANG", "en_US.UTF-8");
            c.env("LC_ALL", "en_US.UTF-8");
            c.env("PYTHONIOENCODING", "utf-8");
            c
        };

        #[cfg(not(target_os = "windows"))]
        let mut cmd = {
            let mut c = Command::new("pnpm");
            c.args(["exec", "tsx"]);
            c.arg(&orchestrator_entry);
            c
        };

        cmd.current_dir(&project_dir)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped());

        let mut child = cmd
            .spawn()
            .map_err(|e| format!("Failed to spawn orchestrator: {}", e))?;
        #[cfg(target_os = "windows")]
        if let Some(job) = job.as_ref() {
            if let Err(e) = job.assign(&child) {
                eprintln!(
                    "[tauri] WARNING: failed to assign sidecar to job object: {}",
                    e
                );
            }
        }

        let stdin = child.stdin.take().ok_or("Failed to get stdin")?;
        let stdout = child.stdout.take().ok_or("Failed to get stdout")?;
        let stderr = child.stderr.take().ok_or("Failed to get stderr")?;

        let pending: Arc<Mutex<HashMap<u64, oneshot::Sender<serde_json::Value>>>> =
            Arc::new(Mutex::new(HashMap::new()));

        // Read stdout (JSON-RPC messages from orchestrator)
        let pending_clone = pending.clone();
        let app_clone = app_handle.clone();
        tokio::spawn(async move {
            let reader = BufReader::new(stdout);
            let mut lines = reader.lines();

            while let Ok(Some(line)) = lines.next_line().await {
                let line = line.trim().to_string();
                if line.is_empty() {
                    tokio::task::yield_now().await;
                    continue;
                }

                match serde_json::from_str::<serde_json::Value>(&line) {
                    Ok(msg) => {
                        if let Some(id) = msg.get("id") {
                            // Response to a request
                            if let Some(id_num) = id.as_u64() {
                                let mut pending = pending_clone.lock().await;
                                if let Some(tx) = pending.remove(&id_num) {
                                    let _ = tx.send(msg);
                                }
                            }
                        } else if let Some(method) = msg.get("method").and_then(|m| m.as_str()) {
                            // Notification — forward as Tauri event
                            let params = msg
                                .get("params")
                                .cloned()
                                .unwrap_or(serde_json::Value::Null);
                            let event_name = method.replace('_', "-");
                            let _ = app_clone.emit(&event_name, params);
                        }
                    }
                    Err(e) => {
                        eprintln!("[tauri] Failed to parse sidecar message: {} — {}", e, line);
                    }
                }
                tokio::task::yield_now().await;
            }

            eprintln!("[tauri] Orchestrator sidecar stdout closed");
            {
                let mut pending = pending_clone.lock().await;
                pending.clear();
            }
            let _ = app_clone.emit(
                "sidecar-error",
                serde_json::json!({
                    "error": "Orchestrator sidecar disconnected before replying. Check sidecar stderr for the root cause."
                }),
            );
        });

        // Read stderr (log messages)
        tokio::spawn(async move {
            let reader = BufReader::new(stderr);
            let mut lines = reader.lines();

            while let Ok(Some(line)) = lines.next_line().await {
                eprintln!("[sidecar] {}", line);
                tokio::task::yield_now().await;
            }
        });

        Ok(Self {
            stdin: Arc::new(Mutex::new(stdin)),
            child: Arc::new(Mutex::new(child)),
            #[cfg(target_os = "windows")]
            _job: job,
            pending,
            next_id: Arc::new(Mutex::new(1)),
        })
    }

    pub async fn send_request(
        &self,
        method: &str,
        params: serde_json::Value,
    ) -> Result<serde_json::Value, String> {
        if !self.is_alive().await {
            return Err("Orchestrator sidecar is not running".to_string());
        }

        let id = {
            let mut next = self.next_id.lock().await;
            let id = *next;
            *next += 1;
            id
        };

        let request = RpcRequest {
            jsonrpc: "2.0".to_string(),
            method: method.to_string(),
            params,
            id: Some(serde_json::Value::Number(id.into())),
        };

        let (tx, rx) = oneshot::channel();
        {
            let mut pending = self.pending.lock().await;
            pending.insert(id, tx);
        }

        let msg = serde_json::to_string(&request).map_err(|e| e.to_string())?;
        {
            let mut stdin = self.stdin.lock().await;
            stdin
                .write_all(format!("{}\n", msg).as_bytes())
                .await
                .map_err(|e| format!("Failed to write to sidecar: {}", e))?;
            stdin.flush().await.map_err(|e| e.to_string())?;
        }

        let response = match timeout(Duration::from_secs(30), rx).await {
            Ok(Ok(resp)) => resp,
            Ok(Err(_)) => {
                // Channel closed — remove pending entry (already consumed by drop, but be safe)
                let mut pending = self.pending.lock().await;
                pending.remove(&id);
                return Err("Sidecar response channel closed".to_string());
            }
            Err(_elapsed) => {
                // Timed out — remove the pending entry to avoid a leak
                let mut pending = self.pending.lock().await;
                pending.remove(&id);
                return Err(format!(
                    "Sidecar request '{}' timed out after 30 seconds",
                    method
                ));
            }
        };

        // Check for error
        if let Some(err) = response.get("error") {
            let msg = err
                .get("message")
                .and_then(|m| m.as_str())
                .unwrap_or("Unknown error");
            return Err(msg.to_string());
        }

        Ok(response
            .get("result")
            .cloned()
            .unwrap_or(serde_json::Value::Null))
    }

    pub async fn send_notification(
        &self,
        method: &str,
        params: serde_json::Value,
    ) -> Result<(), String> {
        if !self.is_alive().await {
            return Err("Orchestrator sidecar is not running".to_string());
        }

        let request = RpcRequest {
            jsonrpc: "2.0".to_string(),
            method: method.to_string(),
            params,
            id: None,
        };

        let msg = serde_json::to_string(&request).map_err(|e| e.to_string())?;
        let mut stdin = self.stdin.lock().await;
        stdin
            .write_all(format!("{}\n", msg).as_bytes())
            .await
            .map_err(|e| format!("Failed to write to sidecar: {}", e))?;
        stdin.flush().await.map_err(|e| e.to_string())?;
        Ok(())
    }

    pub async fn is_alive(&self) -> bool {
        let mut child = self.child.lock().await;
        matches!(child.try_wait(), Ok(None))
    }

    pub async fn shutdown(&self) {
        let mut child = self.child.lock().await;
        #[cfg(target_os = "windows")]
        {
            // The sidecar is launched via `cmd /c pnpm exec tsx ...`, so killing the
            // direct child is not enough. Use taskkill to terminate the whole tree,
            // otherwise the Node.js orchestrator can survive and keep 127.0.0.1:7654 bound.
            if let Some(pid) = child.id() {
                let mut kill_cmd = Command::new("taskkill");
                kill_cmd
                    .args(["/T", "/F", "/PID", &pid.to_string()])
                    .creation_flags(0x08000000); // CREATE_NO_WINDOW

                match kill_cmd.output().await {
                    Ok(output) if output.status.success() => {}
                    Ok(output) => {
                        eprintln!(
                            "[tauri] sidecar taskkill failed: {}",
                            String::from_utf8_lossy(&output.stderr)
                        );
                        let _ = child.kill().await;
                    }
                    Err(e) => {
                        eprintln!("[tauri] sidecar taskkill spawn error: {}", e);
                        let _ = child.kill().await;
                    }
                }
            } else {
                let _ = child.kill().await;
            }
        }

        #[cfg(not(target_os = "windows"))]
        {
            let _ = child.kill().await;
        }

        match timeout(Duration::from_secs(5), child.wait()).await {
            Ok(Ok(_status)) => {}
            Ok(Err(e)) => eprintln!("[tauri] sidecar wait failed during shutdown: {}", e),
            Err(_) => eprintln!("[tauri] sidecar wait timed out during shutdown"),
        }
    }
}

fn resolve_orchestrator_entry(project_dir: &str) -> Result<PathBuf, String> {
    let entry = Path::new(project_dir)
        .join("packages")
        .join("orchestrator")
        .join("src")
        .join("index.ts");

    if entry.exists() {
        return Ok(entry);
    }

    Err(format!(
        "Could not find orchestrator entrypoint at {}",
        entry.display()
    ))
}
