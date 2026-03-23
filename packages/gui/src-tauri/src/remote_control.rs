use std::sync::atomic::{AtomicBool, Ordering};
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
    session_name: Arc<Mutex<Option<String>>>,
    /// Ensures cleanup logic runs exactly once when stdout/stderr tasks race.
    cleanup_done: Arc<AtomicBool>,
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
            session_name: Arc::new(Mutex::new(None)),
            cleanup_done: Arc::new(AtomicBool::new(false)),
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

        // Persist session name and reset cleanup flag
        {
            let mut name = self.session_name.lock().await;
            *name = session_name.clone();
        }
        self.cleanup_done.store(false, Ordering::SeqCst);

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
        let cleanup_flag_stdout = self.cleanup_done.clone();
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

                // Detect connection established.
                // Pattern list centralised here for maintainability if CLI output changes.
                let lower = trimmed.to_lowercase();
                let is_connected = lower.contains("connected")
                    || lower.contains("session active")
                    || lower.contains("connection established");
                if is_connected {
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

            // Process exited — only the first task to reach here runs cleanup
            if !cleanup_flag_stdout.swap(true, Ordering::SeqCst) {
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
            }
        });

        // Monitor stderr for errors and logs
        let status_stderr = self.status.clone();
        let child_stderr = self.child.clone();
        let cleanup_flag_stderr = self.cleanup_done.clone();
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

                // Classify and forward all stderr lines to the GUI log panel.
                let lower = trimmed.to_lowercase();
                let level = if lower.contains("error") && !lower.contains("error handler") {
                    "error"
                } else if lower.contains("warn") {
                    "warn"
                } else {
                    "info"
                };
                let _ = app_stderr.emit("remote-control-log", serde_json::json!({
                    "level": level,
                    "message": trimmed,
                }));
            }

            // stderr closed — only the first task to reach here runs cleanup
            if !cleanup_flag_stderr.swap(true, Ordering::SeqCst) {
                {
                    let mut status_guard = status_stderr.lock().await;
                    *status_guard = RemoteControlStatus::Stopped;
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
            }
        });

        Ok(())
    }

    pub async fn stop(&self) -> Result<(), String> {
        let mut child = self.child.lock().await;
        if let Some(ref mut c) = *child {
            let _ = c.kill().await;
            // Wait for the child process to fully exit (timeout 5s) to avoid zombies.
            // See: https://docs.rs/tokio/latest/tokio/process/struct.Child.html
            let _ = tokio::time::timeout(
                std::time::Duration::from_secs(5),
                c.wait(),
            ).await;
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
        {
            let mut name = self.session_name.lock().await;
            *name = None;
        }
        Ok(())
    }

    /// Returns the current state, reading `session_name` from internal storage.
    pub async fn get_state(&self) -> RemoteControlState {
        let status = self.status.lock().await.clone();
        let session_url = self.session_url.lock().await.clone();
        let session_name = self.session_name.lock().await.clone();
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

/// Extract and validate a URL from a line of text.
/// Only accepts well-formed HTTPS URLs on expected domains (claude.ai, anthropic.com).
/// Uses the `url` crate (https://crates.io/crates/url) for robust parsing.
fn extract_url(line: &str) -> Option<String> {
    if let Some(start) = line.find("https://") {
        let rest = &line[start..];
        let end = rest
            .find(|c: char| c.is_whitespace() || c == '"' || c == '\'' || c == '>' || c == ')')
            .unwrap_or(rest.len());
        let candidate = &rest[..end];
        // Structural validation: must parse as a URL with a recognised host.
        if let Ok(parsed) = url::Url::parse(candidate) {
            if let Some(host) = parsed.host_str() {
                if host.ends_with("claude.ai") || host.ends_with("anthropic.com") {
                    return Some(candidate.to_string());
                }
            }
        }
    }
    None
}
