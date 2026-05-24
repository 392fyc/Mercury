use super::{paths, redact_home};
use notify::{recommended_watcher, EventKind, RecursiveMode, Watcher};
use std::path::PathBuf;
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter};

const DEBOUNCE_MS: u64 = 300;

/// Retry interval for absent (not-yet-created) watch targets.
/// Must be short enough that a newly-created path is picked up quickly
/// (acceptance: "within 1 s"), but long enough not to spin.
const ABSENT_RETRY_SECS: u64 = 1;

/// Tauri event name emitted on debounced FS change. JS side listens via
/// `import { listen } from '@tauri-apps/api/event'; listen(DATA_CHANGED_EVENT, ...)`.
pub const DATA_CHANGED_EVENT: &str = "mercury:data-changed";

/// A single watch target: (path, recursive-mode, human-readable label).
type WatchTarget = (PathBuf, RecursiveMode, &'static str);

/// Spawn a background OS thread that watches the read-side data sources and
/// emits a debounced [`DATA_CHANGED_EVENT`] whenever any of them change.
///
/// The thread owns the `notify::Watcher`; dropping the thread (process exit)
/// stops watching. No explicit shutdown channel for the MVP — follow-up tracked
/// for clean shutdown on `RunEvent::ExitRequested`.
///
/// Returns `()` because all fallible initialization happens inside the spawned
/// thread (watcher creation + `watch()` calls). Failures are logged via stderr
/// (`redact_home`-scrubbed). The GUI continues to work without live events.
pub fn start(app_handle: AppHandle) {
    let jobs = paths::jobs_dir();
    let roster_parent = paths::roster_path().parent().map(|p| p.to_path_buf());
    let lanes_parent = paths::lanes_path().parent().map(|p| p.to_path_buf());

    let mut targets: Vec<WatchTarget> = vec![(jobs, RecursiveMode::Recursive, "jobs")];
    if let Some(p) = roster_parent {
        targets.push((p, RecursiveMode::NonRecursive, "roster-parent"));
    }
    if let Some(p) = lanes_parent {
        targets.push((p, RecursiveMode::NonRecursive, "lanes-parent"));
    }

    thread::spawn(move || {
        run_watch_loop(targets, move || {
            if let Err(e) = app_handle.emit(DATA_CHANGED_EVENT, ()) {
                eprintln!(
                    "[mercury-gui] emit failed: {}",
                    redact_home(&e.to_string())
                );
            }
        });
    });
}

/// Core watch loop — extracted for testability.
///
/// Accepts a list of `targets` (path + mode + label) and an `emit_fn` callback
/// that is invoked whenever a debounced FS-change event fires.  `start()` is
/// the thin caller that supplies real `AppHandle::emit`; tests supply a
/// lightweight counter/channel instead.
///
/// # Absent-watch retry
/// Paths that do not exist at startup are placed in `absent_watches`.  While
/// that list is non-empty the `recv_timeout` idle timeout is shortened to
/// [`ABSENT_RETRY_SECS`], and every timeout tick re-attempts `watcher.watch()`
/// for each absent path.  On success the entry is removed from `absent_watches`
/// and `emit_fn` is called once (so the UI loads freshly available data).
///
/// Two "dirty" concepts deliberately use distinct variable names:
/// - `fs_dirty`  — a debounced FS-change event is pending.
/// - `absent_watches` — paths whose `watch()` has not yet succeeded.
fn run_watch_loop(targets: Vec<WatchTarget>, emit_fn: impl Fn() + Send + 'static) {
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

    // Attempt initial watch for all targets; failures go into absent_watches.
    let mut absent_watches: Vec<WatchTarget> = Vec::new();
    for (path, mode, label) in targets {
        if try_watch_once(&mut watcher, &path, mode, label).is_err() {
            absent_watches.push((path, mode, label));
        }
    }

    // Coalescing receive loop.
    let mut fs_dirty = false;
    let mut last_event = Instant::now();
    loop {
        // Choose timeout: absent paths → short retry interval; FS event pending
        // → sub-debounce poll; idle → long sleep.
        let timeout = if fs_dirty {
            Duration::from_millis(DEBOUNCE_MS / 3)
        } else if !absent_watches.is_empty() {
            Duration::from_secs(ABSENT_RETRY_SECS)
        } else {
            Duration::from_secs(60)
        };

        match rx.recv_timeout(timeout) {
            Ok(Ok(event)) => {
                if is_meaningful(&event.kind) {
                    fs_dirty = true;
                    last_event = Instant::now();
                }
            }
            Ok(Err(e)) => eprintln!(
                "[mercury-gui] watcher event error: {}",
                redact_home(&e.to_string())
            ),
            Err(mpsc::RecvTimeoutError::Timeout) => {
                // --- Flush debounced FS-change event if quiet window has elapsed ---
                if fs_dirty && last_event.elapsed() >= Duration::from_millis(DEBOUNCE_MS) {
                    emit_fn();
                    fs_dirty = false;
                }

                // --- Retry absent watch targets ---
                if !absent_watches.is_empty() {
                    let mut still_absent: Vec<WatchTarget> = Vec::new();
                    for (path, mode, label) in absent_watches.drain(..) {
                        if try_watch_once(&mut watcher, &path, mode, label).is_ok() {
                            // Newly watched path: emit once so UI loads its data.
                            eprintln!(
                                "[mercury-gui] late-watch recovered ({label}) {}",
                                redact_home(&path.display().to_string())
                            );
                            emit_fn();
                        } else {
                            still_absent.push((path, mode, label));
                        }
                    }
                    absent_watches = still_absent;
                }
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }
}

/// Try to register `path` with `watcher`.
///
/// Returns `Ok(())` on success. Returns `Err(())` — a unit error — when the
/// path does not exist or `watcher.watch()` fails; callers only need to know
/// success/failure, not the underlying `notify::Error` variant.
fn try_watch_once(
    watcher: &mut notify::RecommendedWatcher,
    path: &std::path::Path,
    mode: RecursiveMode,
    label: &str,
) -> Result<(), ()> {
    let display = redact_home(&path.display().to_string());
    if !path.exists() {
        eprintln!("[mercury-gui] skip watch ({label}): path absent {display}");
        return Err(());
    }
    if let Err(e) = watcher.watch(path, mode) {
        eprintln!(
            "[mercury-gui] watch failed ({label}) {display}: {}",
            redact_home(&e.to_string())
        );
        return Err(());
    }
    Ok(())
}

fn is_meaningful(kind: &EventKind) -> bool {
    matches!(
        kind,
        EventKind::Create(_) | EventKind::Modify(_) | EventKind::Remove(_)
    )
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};
    use std::time::Duration;

    /// Minimal smoke-test: `try_watch_once` returns `Err` for a non-existent
    /// path and `Ok` after the path is created.
    #[test]
    fn try_watch_once_absent_then_present() {
        use tempfile::tempdir;

        let dir = tempdir().expect("tempdir");
        let sub = dir.path().join("late_subdir");

        let (tx, _rx) = mpsc::channel();
        let mut watcher = recommended_watcher(tx).expect("watcher");

        // Not yet created → Err.
        assert!(
            try_watch_once(&mut watcher, &sub, RecursiveMode::NonRecursive, "test").is_err(),
            "absent path must return Err"
        );

        // Create the directory.
        std::fs::create_dir_all(&sub).expect("create_dir_all");

        // Now exists → Ok.
        assert!(
            try_watch_once(&mut watcher, &sub, RecursiveMode::NonRecursive, "test").is_ok(),
            "present path must return Ok"
        );
    }

    /// Integration test for the absent-watch recovery seam:
    ///
    /// 1. Start `run_watch_loop` against a target that does not yet exist.
    /// 2. The path enters `absent_watches`; no emit fires immediately.
    /// 3. Create the directory.
    /// 4. Within `ABSENT_RETRY_SECS + margin`, the loop retries, succeeds,
    ///    and calls the emit callback once.
    #[test]
    fn absent_watch_recovers_after_path_created() {
        use tempfile::tempdir;

        let dir = tempdir().expect("tempdir");
        let late_dir = dir.path().join("late");

        // Emit counter shared between test thread and watcher thread.
        let emit_count = Arc::new(Mutex::new(0u32));
        let emit_count_clone = Arc::clone(&emit_count);

        let target: WatchTarget = (
            late_dir.clone(),
            RecursiveMode::NonRecursive,
            "late-test",
        );

        // Run the loop in a background thread.
        thread::spawn(move || {
            run_watch_loop(vec![target], move || {
                let mut c = emit_count_clone.lock().unwrap();
                *c += 1;
            });
        });

        // Give the loop a moment to start and classify the path as absent.
        std::thread::sleep(Duration::from_millis(200));

        // Emit count should still be 0 (path absent, no FS events).
        {
            let c = emit_count.lock().unwrap();
            assert_eq!(*c, 0, "no emit expected before path creation");
        }

        // Create the directory that was absent.
        std::fs::create_dir_all(&late_dir).expect("create late_dir");

        // Wait long enough for at least one retry cycle: ABSENT_RETRY_SECS (1 s)
        // plus a 2 s margin for scheduler variance on CI.
        std::thread::sleep(Duration::from_secs(3));

        // The loop should have recovered the watch and emitted once.
        let c = emit_count.lock().unwrap();
        assert!(
            *c >= 1,
            "expected at least 1 emit after late-created path recovered; got {c}"
        );
    }
}
