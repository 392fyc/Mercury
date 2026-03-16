use std::collections::HashMap;
use std::sync::Arc;
use tauri::Emitter;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::{Mutex, oneshot};

use crate::types::RpcRequest;

#[derive(Clone)]
pub struct SidecarManager {
    stdin: Arc<Mutex<tokio::process::ChildStdin>>,
    child: Arc<Mutex<Child>>,
    pending: Arc<Mutex<HashMap<u64, oneshot::Sender<serde_json::Value>>>>,
    next_id: Arc<Mutex<u64>>,
}

impl SidecarManager {
    pub async fn spawn(
        app_handle: tauri::AppHandle,
        project_dir: String,
    ) -> Result<Self, String> {
        // In dev mode, use pnpm exec tsx to run the orchestrator.
        // Using pnpm instead of npx avoids npm warn noise from inherited env vars.
        // On Windows, pnpm is a .cmd script so we must run via cmd.exe.
        #[cfg(target_os = "windows")]
        let mut cmd = {
            let mut c = Command::new("cmd");
            c.args(["/c", "pnpm", "exec", "tsx", "packages/orchestrator/src/index.ts"]);
            c.creation_flags(0x08000000); // CREATE_NO_WINDOW
            c
        };

        #[cfg(not(target_os = "windows"))]
        let mut cmd = {
            let mut c = Command::new("pnpm");
            c.args(["exec", "tsx", "packages/orchestrator/src/index.ts"]);
            c
        };

        cmd.current_dir(&project_dir)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped());

        let mut child = cmd.spawn().map_err(|e| format!("Failed to spawn orchestrator: {}", e))?;

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
                            let params = msg.get("params").cloned().unwrap_or(serde_json::Value::Null);
                            let event_name = method.replace('_', "-");
                            let _ = app_clone.emit(&event_name, params);
                        }
                    }
                    Err(e) => {
                        eprintln!("[tauri] Failed to parse sidecar message: {} — {}", e, line);
                    }
                }
            }
        });

        // Read stderr (log messages)
        tokio::spawn(async move {
            let reader = BufReader::new(stderr);
            let mut lines = reader.lines();

            while let Ok(Some(line)) = lines.next_line().await {
                eprintln!("[sidecar] {}", line);
            }
        });

        Ok(Self {
            stdin: Arc::new(Mutex::new(stdin)),
            child: Arc::new(Mutex::new(child)),
            pending,
            next_id: Arc::new(Mutex::new(1)),
        })
    }

    pub async fn send_request(
        &self,
        method: &str,
        params: serde_json::Value,
    ) -> Result<serde_json::Value, String> {
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

        let response = rx
            .await
            .map_err(|_| "Sidecar response channel closed".to_string())?;

        // Check for error
        if let Some(err) = response.get("error") {
            let msg = err
                .get("message")
                .and_then(|m| m.as_str())
                .unwrap_or("Unknown error");
            return Err(msg.to_string());
        }

        Ok(response.get("result").cloned().unwrap_or(serde_json::Value::Null))
    }

    pub async fn send_notification(
        &self,
        method: &str,
        params: serde_json::Value,
    ) -> Result<(), String> {
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
        let _ = child.kill().await;
    }
}
