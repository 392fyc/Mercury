use std::sync::Arc;
use tauri::Emitter;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::Mutex;

/// Manages a `claude remote-control` child process.
/// Parses stdout for session URL, emits Tauri events for GUI updates.
#[derive(Clone)]
pub struct RemoteControlManager {
    child: Arc<Mutex<Option<Child>>>,
    session_url: Arc<Mutex<Option<String>>>,
    status: Arc<Mutex<RemoteControlStatus>>,
}

#[derive(Clone, Debug, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RemoteControlStatus {
    Stopped,
    Starting,
    WaitingForConnection,
    Connected,
    Error(String),
}

#[derive(Clone, Debug, serde::Serialize)]
pub struct RemoteControlState {
    pub status: RemoteControlStatus,
    pub session_url: Option<String>,
    pub session_name: Option<String>,
}

impl RemoteControlManager {
    pub fn new() -> Self {
        Self {
            child: Arc::new(Mutex::new(None)),
            session_url: Arc::new(Mutex::new(None)),
            status: Arc::new(Mutex::new(RemoteControlStatus::Stopped)),
        }
    }

    pub async fn start(
        &self,
        app_handle: tauri::AppHandle,
        project_dir: String,
        session_name: Option<String>,
    ) -> Result<(), String> {
        // Check if already running
        {
            let child = self.child.lock().await;
            if child.is_some() {
                return Err("Remote control is already running".to_string());
            }
        }

        // Update status
        {
            let mut status = self.status.lock().await;
            *status = RemoteControlStatus::Starting;
        }
        let _ = app_handle.emit("remote-control-status", RemoteControlState {
            status: RemoteControlStatus::Starting,
            session_url: None,
            session_name: session_name.clone(),
        });

        // Build command: `claude remote-control --verbose`
        // On Windows, claude is a .cmd script so we must run via cmd.exe.
        #[cfg(target_os = "windows")]
        let mut cmd = {
            let mut c = Command::new("cmd");
            c.args(["/c", "claude", "remote-control"]);
            if let Some(ref name) = session_name {
                c.args(["--name", name]);
            }
            c.arg("--verbose");
            c.creation_flags(0x08000000); // CREATE_NO_WINDOW
            c
        };

        #[cfg(not(target_os = "windows"))]
        let mut cmd = {
            let mut c = Command::new("claude");
            c.arg("remote-control");
            if let Some(ref name) = session_name {
                c.args(["--name", name]);
            }
            c.arg("--verbose");
            c
        };

        cmd.current_dir(&project_dir)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped());

        let mut child = cmd
            .spawn()
            .map_err(|e| format!("Failed to spawn claude remote-control: {}", e))?;

        let stdout = child.stdout.take().ok_or("Failed to get stdout")?;
        let stderr = child.stderr.take().ok_or("Failed to get stderr")?;

        // Store child process
        {
            let mut guard = self.child.lock().await;
            *guard = Some(child);
        }

        // Monitor stdout for session URL and status changes
        let url_clone = self.session_url.clone();
        let status_clone = self.status.clone();
        let child_clone = self.child.clone();
        let app_stdout = app_handle.clone();
        let name_for_stdout = session_name.clone();
        tokio::spawn(async move {
            let reader = BufReader::new(stdout);
            let mut lines = reader.lines();

            while let Ok(Some(line)) = lines.next_line().await {
                let trimmed = line.trim().to_string();
                if trimmed.is_empty() {
                    continue;
                }

                eprintln!("[remote-control stdout] {}", trimmed);

                // Detect session URL (typically contains claude.ai/code or a URL pattern)
                if trimmed.contains("https://") {
                    // Extract URL from the line
                    if let Some(url) = extract_url(&trimmed) {
                        {
                            let mut url_guard = url_clone.lock().await;
                            *url_guard = Some(url.clone());
                        }
                        {
                            let mut status_guard = status_clone.lock().await;
                            *status_guard = RemoteControlStatus::WaitingForConnection;
                        }
                        let _ = app_stdout.emit("remote-control-status", RemoteControlState {
                            status: RemoteControlStatus::WaitingForConnection,
                            session_url: Some(url.clone()),
                            session_name: name_for_stdout.clone(),
                        });
                        let _ = app_stdout.emit("remote-control-url", serde_json::json!({
                            "url": url,
                        }));
                    }
                }

                // Detect connection established
                if trimmed.to_lowercase().contains("connected")
                    || trimmed.to_lowercase().contains("session active")
                {
                    {
                        let mut status_guard = status_clone.lock().await;
                        *status_guard = RemoteControlStatus::Connected;
                    }
                    let url_val = url_clone.lock().await.clone();
                    let _ = app_stdout.emit("remote-control-status", RemoteControlState {
                        status: RemoteControlStatus::Connected,
                        session_url: url_val,
                        session_name: name_for_stdout.clone(),
                    });
                }
            }

            // Process exited
            {
                let mut status_guard = status_clone.lock().await;
                *status_guard = RemoteControlStatus::Stopped;
            }
            {
                let mut child_guard = child_clone.lock().await;
                *child_guard = None;
            }
            let _ = app_stdout.emit("remote-control-status", RemoteControlState {
                status: RemoteControlStatus::Stopped,
                session_url: None,
                session_name: name_for_stdout.clone(),
            });
        });

        // Monitor stderr for errors and logs
        let status_stderr = self.status.clone();
        let child_stderr = self.child.clone();
        let app_stderr = app_handle.clone();
        let name_for_stderr = session_name;
        tokio::spawn(async move {
            let reader = BufReader::new(stderr);
            let mut lines = reader.lines();

            while let Ok(Some(line)) = lines.next_line().await {
                let trimmed = line.trim().to_string();
                if trimmed.is_empty() {
                    continue;
                }
                eprintln!("[remote-control stderr] {}", trimmed);

                // Detect fatal errors
                if trimmed.to_lowercase().contains("error")
                    && !trimmed.to_lowercase().contains("error handler")
                {
                    let _ = app_stderr.emit("remote-control-log", serde_json::json!({
                        "level": "error",
                        "message": trimmed,
                    }));
                }
            }

            // stderr closed — process likely exited
            {
                let mut status_guard = status_stderr.lock().await;
                if !matches!(*status_guard, RemoteControlStatus::Stopped) {
                    *status_guard = RemoteControlStatus::Stopped;
                }
            }
            {
                let mut child_guard = child_stderr.lock().await;
                *child_guard = None;
            }
            let _ = app_stderr.emit("remote-control-status", RemoteControlState {
                status: RemoteControlStatus::Stopped,
                session_url: None,
                session_name: name_for_stderr,
            });
        });

        Ok(())
    }

    pub async fn stop(&self) -> Result<(), String> {
        let mut child = self.child.lock().await;
        if let Some(ref mut c) = *child {
            let _ = c.kill().await;
            *child = None;
        }
        {
            let mut status = self.status.lock().await;
            *status = RemoteControlStatus::Stopped;
        }
        {
            let mut url = self.session_url.lock().await;
            *url = None;
        }
        Ok(())
    }

    pub async fn get_state(&self, session_name: Option<String>) -> RemoteControlState {
        let status = self.status.lock().await.clone();
        let session_url = self.session_url.lock().await.clone();
        RemoteControlState {
            status,
            session_url,
            session_name,
        }
    }

    pub async fn is_running(&self) -> bool {
        let child = self.child.lock().await;
        child.is_some()
    }
}

/// Extract a URL from a line of text.
fn extract_url(line: &str) -> Option<String> {
    // Find the start of https://
    if let Some(start) = line.find("https://") {
        // Find the end of the URL (space, newline, or end of string)
        let rest = &line[start..];
        let end = rest
            .find(|c: char| c.is_whitespace() || c == '"' || c == '\'' || c == '>' || c == ')')
            .unwrap_or(rest.len());
        let url = &rest[..end];
        if url.len() > 10 {
            return Some(url.to_string());
        }
    }
    None
}
