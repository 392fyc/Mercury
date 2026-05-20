use super::{paths, redact_home};
use notify::{recommended_watcher, EventKind, RecursiveMode, Watcher};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter};

const DEBOUNCE_MS: u64 = 300;

/// Tauri event name emitted on debounced FS change. JS side listens via
/// `import { listen } from '@tauri-apps/api/event'; listen(DATA_CHANGED_EVENT, ...)`.
pub const DATA_CHANGED_EVENT: &str = "mercury:data-changed";

/// Spawn a background OS thread that watches the read-side data sources and
/// emits a debounced [`DATA_CHANGED_EVENT`] whenever any of them change.
///
/// The thread owns the `notify::Watcher`; dropping the thread (process exit)
/// stops watching. No explicit shutdown channel for the MVP — follow-up tracked
/// for clean shutdown on `RunEvent::ExitRequested`.
pub fn start(app_handle: AppHandle) -> Result<(), notify::Error> {
    let jobs = paths::jobs_dir();
    let roster_parent = paths::roster_path().parent().map(|p| p.to_path_buf());
    let lanes_parent = paths::lanes_path().parent().map(|p| p.to_path_buf());

    thread::spawn(move || {
        let (tx, rx) = mpsc::channel();
        let mut watcher = match recommended_watcher(tx) {
            Ok(w) => w,
            Err(e) => {
                eprintln!(
                    "[mercury-gui] watcher init failed: {}",
                    redact_home(&e.to_string())
                );
                return;
            }
        };

        // Watch the jobs dir recursively (sessions are subdirs each with their own state.json).
        // Missing paths are non-fatal: log + skip; GUI still works against whichever paths exist.
        let _ = try_watch(&mut watcher, &jobs, RecursiveMode::Recursive, "jobs");
        if let Some(p) = roster_parent {
            let _ = try_watch(&mut watcher, &p, RecursiveMode::NonRecursive, "roster-parent");
        }
        if let Some(p) = lanes_parent {
            let _ = try_watch(&mut watcher, &p, RecursiveMode::NonRecursive, "lanes-parent");
        }

        // Coalescing receive loop: drain raw events until DEBOUNCE_MS quiet window,
        // then emit a single notification to the frontend.
        let mut pending = false;
        let mut last_event = Instant::now();
        loop {
            let timeout = if pending {
                Duration::from_millis(DEBOUNCE_MS / 3)
            } else {
                Duration::from_secs(60)
            };
            match rx.recv_timeout(timeout) {
                Ok(Ok(event)) => {
                    if is_meaningful(&event.kind) {
                        pending = true;
                        last_event = Instant::now();
                    }
                }
                Ok(Err(e)) => eprintln!(
                    "[mercury-gui] watcher event error: {}",
                    redact_home(&e.to_string())
                ),
                Err(mpsc::RecvTimeoutError::Timeout) => {
                    if pending && last_event.elapsed() >= Duration::from_millis(DEBOUNCE_MS) {
                        if let Err(e) = app_handle.emit(DATA_CHANGED_EVENT, ()) {
                            eprintln!(
                                "[mercury-gui] emit failed: {}",
                                redact_home(&e.to_string())
                            );
                        }
                        pending = false;
                    }
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => break,
            }
        }
    });
    Ok(())
}

fn try_watch(
    watcher: &mut notify::RecommendedWatcher,
    path: &std::path::Path,
    mode: RecursiveMode,
    label: &str,
) -> Result<(), notify::Error> {
    let display = redact_home(&path.display().to_string());
    // MVP limitation: late-created paths don't auto-rewatch; restart GUI to
    // pick them up. Tracked at https://github.com/392fyc/Mercury/issues/423.
    if !path.exists() {
        eprintln!("[mercury-gui] skip watch ({label}): path absent {display}");
        return Ok(());
    }
    if let Err(e) = watcher.watch(path, mode) {
        eprintln!(
            "[mercury-gui] watch failed ({label}) {display}: {}",
            redact_home(&e.to_string())
        );
        return Err(e);
    }
    Ok(())
}

fn is_meaningful(kind: &EventKind) -> bool {
    matches!(
        kind,
        EventKind::Create(_) | EventKind::Modify(_) | EventKind::Remove(_)
    )
}
