use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use tauri::Emitter;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::Mutex;

#[cfg(target_os = "windows")]
use crate::windows_job::ProcessJob;

/// Manages a `claude remote-control` child process.
/// Parses stdout for session URL, emits Tauri events for GUI updates.
///
/// Thread-safety design:
/// - `start()` holds the child lock across the entire spawn to prevent concurrent spawns.
/// - A monotonic `generation` counter ties each spawn's cleanup tasks to the correct session,
///   so stale stdout/stderr watchers from a previous process cannot corrupt the current state.
///
/// References:
/// - AtomicU64: https://docs.rs/rustc-std-workspace-std/latest/std/sync/atomic/struct.AtomicU64.html
/// - tokio Child: https://docs.rs/tokio/latest/tokio/process/struct.Child.html
/// - url crate: https://crates.io/crates/url
#[derive(Clone)]
pub struct RemoteControlManager {
    child: Arc<Mutex<Option<Child>>>,
    #[cfg(target_os = "windows")]
    job: Arc<Mutex<Option<ProcessJob>>>,
    session_url: Arc<Mutex<Option<String>>>,
    status: Arc<Mutex<RemoteControlStatus>>,
    session_name: Arc<Mutex<Option<String>>>,
    /// Ensures cleanup logic runs exactly once per generation.
    cleanup_done: Arc<AtomicBool>,
    /// Monotonic counter incremented on every `start()`. Cleanup tasks compare their
    /// captured generation against the current value to avoid cross-session corruption.
    generation: Arc<AtomicU64>,
}

/// Remote control session status.
/// All variants are unit variants so they serialise as plain strings
/// (e.g. `"stopped"`, `"error"`), matching the front-end TypeScript union type.
#[derive(Clone, Debug, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RemoteControlStatus {
    Stopped,
    Starting,
    WaitingForConnection,
    Connected,
    Error,
}

#[derive(Clone, Debug, serde::Serialize)]
pub struct RemoteControlState {
    pub status: RemoteControlStatus,
    pub session_url: Option<String>,
    pub session_name: Option<String>,
    /// Present only when `status == "error"`; carries the error description.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_message: Option<String>,
}

impl RemoteControlManager {
    pub fn new() -> Self {
        Self {
            child: Arc::new(Mutex::new(None)),
            #[cfg(target_os = "windows")]
            job: Arc::new(Mutex::new(None)),
            session_url: Arc::new(Mutex::new(None)),
            status: Arc::new(Mutex::new(RemoteControlStatus::Stopped)),
            session_name: Arc::new(Mutex::new(None)),
            cleanup_done: Arc::new(AtomicBool::new(false)),
            generation: Arc::new(AtomicU64::new(0)),
        }
    }

    /// Start a `claude remote-control` session.
    ///
    /// The child lock is held across the entire spawn to guarantee atomicity:
    /// no concurrent `start()` can slip through between the "is running?" check and
    /// the child handle being stored.
    pub async fn start(
        &self,
        app_handle: tauri::AppHandle,
        project_dir: String,
        session_name: Option<String>,
    ) -> Result<(), String> {
        // Hold the child lock for the ENTIRE start sequence to prevent concurrent spawns.
        let mut child_guard = self.child.lock().await;

        if child_guard.is_some() {
            return Err("Remote control is already running".to_string());
        }

        // Bump generation so any lingering cleanup tasks from a previous session become no-ops.
        let gen = self.generation.fetch_add(1, Ordering::SeqCst) + 1;
        self.cleanup_done.store(false, Ordering::SeqCst);

        // Persist session name
        {
            let mut name = self.session_name.lock().await;
            *name = session_name.clone();
        }

        // Update status to Starting
        {
            let mut status = self.status.lock().await;
            *status = RemoteControlStatus::Starting;
        }
        let _ = app_handle.emit(
            "remote-control-status",
            RemoteControlState {
                status: RemoteControlStatus::Starting,
                session_url: None,
                session_name: session_name.clone(),
                error_message: None,
            },
        );

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
                                          // Force UTF-8 encoding to prevent codepage 936/GBK garbling on CJK Windows.
                                          // Mirrors sidecar.rs environment setup for consistency.
            c.env("LANG", "en_US.UTF-8");
            c.env("LC_ALL", "en_US.UTF-8");
            c.env("PYTHONIOENCODING", "utf-8");
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

        let mut child = match cmd.spawn() {
            Ok(c) => c,
            Err(e) => {
                // Rollback status on spawn failure so the state machine stays clean.
                {
                    let mut status = self.status.lock().await;
                    *status = RemoteControlStatus::Stopped;
                }
                {
                    let mut name = self.session_name.lock().await;
                    *name = None;
                }
                let err_msg = format!("Failed to spawn claude remote-control: {}", e);
                let _ = app_handle.emit(
                    "remote-control-status",
                    RemoteControlState {
                        status: RemoteControlStatus::Error,
                        session_url: None,
                        session_name: None,
                        error_message: Some(err_msg.clone()),
                    },
                );
                return Err(err_msg);
            }
        };
        #[cfg(target_os = "windows")]
        {
            match ProcessJob::new_kill_on_close() {
                Ok(job) => {
                    if let Err(e) = job.assign(&child) {
                        eprintln!(
                            "[remote-control] WARNING: failed to assign process to job object: {}",
                            e
                        );
                    }
                    let mut job_guard = self.job.lock().await;
                    *job_guard = Some(job);
                }
                Err(e) => {
                    eprintln!(
                        "[remote-control] WARNING: failed to create job object: {}",
                        e
                    );
                }
            }
        }

        let stdout = child.stdout.take().ok_or("Failed to get stdout")?;
        let stderr = child.stderr.take().ok_or("Failed to get stderr")?;

        // Store child process (still under the same lock — atomic).
        *child_guard = Some(child);
        // Release the child lock now.
        drop(child_guard);

        // Monitor stdout for session URL and status changes
        let url_clone = self.session_url.clone();
        let status_clone = self.status.clone();
        let child_clone = self.child.clone();
        let cleanup_flag_stdout = self.cleanup_done.clone();
        let generation_stdout = self.generation.clone();
        #[cfg(target_os = "windows")]
        let job_stdout = self.job.clone();
        let app_stdout = app_handle.clone();
        let name_for_stdout = session_name.clone();
        let session_name_stdout = self.session_name.clone();
        tokio::spawn(async move {
            let reader = BufReader::new(stdout);
            let mut lines = reader.lines();

            while let Ok(Some(line)) = lines.next_line().await {
                // If a newer generation has started, this task is stale — bail out.
                if generation_stdout.load(Ordering::SeqCst) != gen {
                    return;
                }

                // Forward the raw line (preserving whitespace for QR/ASCII art) to the GUI.
                let _ = app_stdout.emit(
                    "remote-control-log",
                    serde_json::json!({
                        "level": "stdout",
                        "message": &line,
                    }),
                );

                let trimmed = line.trim().to_string();
                if trimmed.is_empty() {
                    continue;
                }

                // Debug logging — redact session URLs to avoid leaking pairing credentials.
                #[cfg(debug_assertions)]
                eprintln!("[remote-control stdout] {}", redact_url(&trimmed));

                // Detect session URL (typically contains claude.ai/code or a URL pattern)
                if trimmed.contains("https://") {
                    if let Some(url) = extract_url(&trimmed) {
                        {
                            let mut url_guard = url_clone.lock().await;
                            *url_guard = Some(url.clone());
                        }
                        {
                            let mut status_guard = status_clone.lock().await;
                            *status_guard = RemoteControlStatus::WaitingForConnection;
                        }
                        let _ = app_stdout.emit(
                            "remote-control-status",
                            RemoteControlState {
                                status: RemoteControlStatus::WaitingForConnection,
                                session_url: Some(url.clone()),
                                session_name: name_for_stdout.clone(),
                                error_message: None,
                            },
                        );
                        let _ = app_stdout.emit(
                            "remote-control-url",
                            serde_json::json!({
                                "url": url,
                            }),
                        );
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
                    let _ = app_stdout.emit(
                        "remote-control-status",
                        RemoteControlState {
                            status: RemoteControlStatus::Connected,
                            session_url: url_val,
                            session_name: name_for_stdout.clone(),
                            error_message: None,
                        },
                    );
                }
            }

            // Process exited — only the first task to reach here runs cleanup,
            // and only if this is still the current generation.
            if generation_stdout.load(Ordering::SeqCst) == gen
                && !cleanup_flag_stdout.swap(true, Ordering::SeqCst)
            {
                {
                    let mut status_guard = status_clone.lock().await;
                    *status_guard = RemoteControlStatus::Stopped;
                }
                {
                    let mut url_guard = url_clone.lock().await;
                    *url_guard = None;
                }
                {
                    let mut child_guard = child_clone.lock().await;
                    *child_guard = None;
                }
                {
                    let mut name_guard = session_name_stdout.lock().await;
                    *name_guard = None;
                }
                #[cfg(target_os = "windows")]
                {
                    let mut job_guard = job_stdout.lock().await;
                    *job_guard = None;
                }
                let _ = app_stdout.emit(
                    "remote-control-status",
                    RemoteControlState {
                        status: RemoteControlStatus::Stopped,
                        session_url: None,
                        session_name: None,
                        error_message: None,
                    },
                );
            }
        });

        // Monitor stderr for errors and logs
        let status_stderr = self.status.clone();
        let url_stderr = self.session_url.clone();
        let child_stderr = self.child.clone();
        let cleanup_flag_stderr = self.cleanup_done.clone();
        let generation_stderr = self.generation.clone();
        #[cfg(target_os = "windows")]
        let job_stderr = self.job.clone();
        let app_stderr = app_handle.clone();
        let session_name_stderr = self.session_name.clone();
        drop(session_name);
        tokio::spawn(async move {
            let reader = BufReader::new(stderr);
            let mut lines = reader.lines();

            while let Ok(Some(line)) = lines.next_line().await {
                if generation_stderr.load(Ordering::SeqCst) != gen {
                    return;
                }

                let trimmed = line.trim().to_string();
                if trimmed.is_empty() {
                    continue;
                }

                #[cfg(debug_assertions)]
                eprintln!("[remote-control stderr] {}", redact_url(&trimmed));

                // Classify and forward all stderr lines to the GUI log panel.
                let lower = trimmed.to_lowercase();
                let level = if lower.contains("error") && !lower.contains("error handler") {
                    "error"
                } else if lower.contains("warn") {
                    "warn"
                } else {
                    "info"
                };
                let _ = app_stderr.emit(
                    "remote-control-log",
                    serde_json::json!({
                        "level": level,
                        "message": trimmed,
                    }),
                );
            }

            // stderr closed — only the first task to reach here runs cleanup
            if generation_stderr.load(Ordering::SeqCst) == gen
                && !cleanup_flag_stderr.swap(true, Ordering::SeqCst)
            {
                {
                    let mut status_guard = status_stderr.lock().await;
                    *status_guard = RemoteControlStatus::Stopped;
                }
                {
                    let mut url_guard = url_stderr.lock().await;
                    *url_guard = None;
                }
                {
                    let mut child_guard = child_stderr.lock().await;
                    *child_guard = None;
                }
                {
                    let mut name_guard = session_name_stderr.lock().await;
                    *name_guard = None;
                }
                #[cfg(target_os = "windows")]
                {
                    let mut job_guard = job_stderr.lock().await;
                    *job_guard = None;
                }
                let _ = app_stderr.emit(
                    "remote-control-status",
                    RemoteControlState {
                        status: RemoteControlStatus::Stopped,
                        session_url: None,
                        session_name: None,
                        error_message: None,
                    },
                );
            }
        });

        Ok(())
    }

    /// Stop the remote-control session, killing the child process and resetting all state.
    ///
    /// On Windows, `child.kill()` only terminates the direct child (cmd.exe), leaving the
    /// actual `claude` process alive as an orphan. We use `taskkill /T /F /PID` to kill
    /// the entire process tree instead.
    pub async fn stop(&self) -> Result<(), String> {
        let mut child = self.child.lock().await;
        if let Some(ref mut c) = *child {
            #[cfg(target_os = "windows")]
            {
                // taskkill /T /F /PID kills the entire process tree rooted at the child PID.
                // This ensures cmd.exe, claude.exe, and any grandchild processes are all terminated.
                if let Some(pid) = c.id() {
                    let mut kill_cmd = Command::new("taskkill");
                    kill_cmd
                        .args(["/T", "/F", "/PID", &pid.to_string()])
                        .creation_flags(0x08000000); // CREATE_NO_WINDOW
                    let taskkill_ok = match kill_cmd.output().await {
                        Ok(output) if output.status.success() => true,
                        Ok(output) => {
                            eprintln!(
                                "[remote-control] taskkill failed: {}",
                                String::from_utf8_lossy(&output.stderr)
                            );
                            false
                        }
                        Err(e) => {
                            eprintln!("[remote-control] taskkill spawn error: {}", e);
                            false
                        }
                    };
                    if !taskkill_ok {
                        // Best-effort fallback: try normal kill if taskkill failed.
                        let _ = c.kill().await;
                    }
                } else {
                    // id() returns None when the child has already been reaped by the OS
                    // or was never successfully spawned. Attempt normal kill as best-effort.
                    let _ = c.kill().await;
                }
            }
            #[cfg(not(target_os = "windows"))]
            {
                let _ = c.kill().await;
            }
            // Wait for the child process to fully exit (timeout 5s) to avoid zombies.
            // If the child does not exit within the timeout, treat stop as failed.
            match tokio::time::timeout(std::time::Duration::from_secs(5), c.wait()).await {
                Ok(_) => {
                    *child = None;
                }
                Err(_) => {
                    // Child still alive after timeout — do NOT clear state.
                    // Return error so the GUI knows the process may still be running.
                    return Err("Stop timed out: child process may still be running".to_string());
                }
            }
        }
        #[cfg(target_os = "windows")]
        {
            let mut job = self.job.lock().await;
            *job = None;
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
            error_message: None,
        }
    }

    /// Returns whether a child process is currently held.
    pub async fn is_running(&self) -> bool {
        let child = self.child.lock().await;
        child.is_some()
    }
}

/// Extract and validate a URL from a line of text.
/// Only accepts well-formed HTTPS URLs on the exact `claude.ai` domain (or subdomains).
/// Uses the `url` crate (<https://crates.io/crates/url>) for robust parsing.
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
                // Exact match or subdomain — rejects lookalikes like "evilclaude.ai".
                if host == "claude.ai" || host.ends_with(".claude.ai") {
                    return Some(candidate.to_string());
                }
            }
        }
    }
    None
}

/// Replace any `https://...` URL in the line with a redacted placeholder.
/// Used for debug logging to avoid leaking session pairing credentials.
/// Only compiled in debug builds via `#[cfg(debug_assertions)]`.
/// See: https://doc.rust-lang.org/reference/conditional-compilation.html
#[cfg(debug_assertions)]
fn redact_url(line: &str) -> String {
    if let Some(start) = line.find("https://") {
        let rest = &line[start..];
        let end = rest.find(|c: char| c.is_whitespace()).unwrap_or(rest.len());
        format!("{}[REDACTED_URL]{}", &line[..start], &line[start + end..])
    } else {
        line.to_string()
    }
}
