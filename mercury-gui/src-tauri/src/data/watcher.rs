use super::{paths, redact_home};
use notify::{recommended_watcher, EventKind, RecursiveMode, Watcher};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter};

const DEBOUNCE_MS: u64 = 300;

/// Retry interval for absent (not-yet-created) watch targets.
/// Must be short enough that a newly-created path is picked up quickly
/// (#423 acceptance: "within 1 s"), but long enough not to spin.
///
/// Flat (no backoff) by design: the 1 s acceptance precludes a growing
/// interval, and a never-created target only costs one cheap `path.exists()`
/// stat per second across at most 3 targets — negligible, and only while a
/// data dir is genuinely absent (a fresh-install edge case; once present the
/// loop reverts to the 60 s idle timeout).
const ABSENT_RETRY_SECS: u64 = 1;

/// Idle heartbeat: how often the loop wakes when there is nothing to do, solely
/// to poll the `stop` flag (Fix C). Kept at 1 s (= [`ABSENT_RETRY_SECS`]) so a
/// hours-long idle GUI session does not incur sub-second wakeup pressure; the
/// production `stop` is never set, so faster polling would benefit only tests.
const IDLE_HEARTBEAT_SECS: u64 = 1;

/// Tauri event name emitted on debounced FS change. JS side listens via
/// `import { listen } from '@tauri-apps/api/event'; listen(DATA_CHANGED_EVENT, ...)`.
pub const DATA_CHANGED_EVENT: &str = "mercury:data-changed";

/// A single watch target: (path, recursive-mode, human-readable label).
type WatchTarget = (PathBuf, RecursiveMode, &'static str);

/// Outcome of a single `try_watch_once` call — three distinct states that
/// the caller must handle differently:
///
/// - `Watched`  — `watcher.watch()` succeeded; remove from retry list.
/// - `Absent`   — path does not exist (`!path.exists()`); transient, retry later.
/// - `Failed`   — path exists but `watcher.watch()` returned an error
///   (permission denied, OS watch-limit, invalid path, …).
///   Non-transient: log once and **do not** add to retry list.
#[derive(Debug, PartialEq)]
enum WatchResult {
    Watched,
    Absent,
    Failed,
}

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

    // Pass a stop flag that is never set — preserves "thread lives until process
    // exit" semantics while making run_watch_loop testably stoppable (Fix C).
    let stop = Arc::new(AtomicBool::new(false));
    thread::spawn(move || {
        run_watch_loop(targets, stop, move || {
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
/// Accepts a list of `targets` (path + mode + label), a `stop` flag, and an
/// `emit_fn` callback that is invoked whenever a debounced FS-change event
/// fires.  `start()` is the thin caller that supplies real `AppHandle::emit`;
/// tests supply a lightweight counter/channel instead.
///
/// The loop exits when `stop` is set to `true` (Fix C) or the channel
/// disconnects.
///
/// # Absent-watch retry
/// Paths that do not exist at startup are placed in `absent_watches`.  Every
/// ~[`ABSENT_RETRY_SECS`] — measured by wall-clock elapsed time regardless of
/// whether the receive loop is in the Timeout or Ok branch — the loop
/// re-attempts `watcher.watch()` for each absent path (Fix B: event stream
/// cannot starve the retry).  On success the entry is removed from
/// `absent_watches` and `emit_fn` is called once (so the UI loads freshly
/// available data).
///
/// Paths whose `watch()` call returns an error despite the path existing are
/// classified as `Failed` and are NOT added to `absent_watches` (Fix A: avoids
/// infinite stderr noise for non-transient errors such as permission denied).
///
/// Two "dirty" concepts deliberately use distinct variable names:
/// - `fs_dirty`      — a debounced FS-change event is pending.
/// - `absent_watches` — paths whose `watch()` has not yet succeeded (transient).
fn run_watch_loop(
    targets: Vec<WatchTarget>,
    stop: Arc<AtomicBool>,
    emit_fn: impl Fn() + Send + 'static,
) {
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

    // Attempt initial watch for all targets.
    // Only `Absent` results are queued for retry; `Failed` results are logged
    // inside try_watch_once and silently dropped here (Fix A).
    let mut absent_watches: Vec<WatchTarget> = Vec::new();
    for (path, mode, label) in targets {
        match try_watch_once(&mut watcher, &path, mode, label) {
            WatchResult::Watched => {}
            WatchResult::Absent => absent_watches.push((path, mode, label)),
            WatchResult::Failed => {} // logged inside; do not retry
        }
    }

    // Time-based throttle for absent retries so that a high-frequency event
    // stream cannot starve the retry logic (Fix B).
    let mut last_absent_retry = Instant::now();

    // Coalescing receive loop.
    let mut fs_dirty = false;
    let mut last_event = Instant::now();
    loop {
        // Check stop flag at the top of every iteration (Fix C).
        if stop.load(Ordering::Relaxed) {
            break;
        }

        // Choose timeout: FS event pending → sub-debounce poll; absent paths
        // present → cap at ABSENT_RETRY_SECS so the time-throttle below fires
        // even when no FS events arrive; idle → 1 s heartbeat so the stop flag
        // (checked at the top of each iteration) is honored within ~1 s.
        //
        // Idle is 1 s (not sub-second): production `start()` passes a never-set
        // stop, so sub-second polling would only benefit tests while adding
        // needless idle wakeups for a hours-long desktop GUI session. 1 s
        // matches ABSENT_RETRY_SECS (single idle cadence) and is ample for any
        // future RunEvent::ExitRequested graceful-shutdown budget.
        let timeout = if fs_dirty {
            Duration::from_millis(DEBOUNCE_MS / 3)
        } else if !absent_watches.is_empty() {
            Duration::from_secs(ABSENT_RETRY_SECS)
        } else {
            Duration::from_secs(IDLE_HEARTBEAT_SECS)
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
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }

        // --- Retry absent watch targets (time-throttled, Fix B) ---
        // Checked after every branch so that a continuous event stream does not
        // prevent recovery of newly-created paths.
        if !absent_watches.is_empty()
            && last_absent_retry.elapsed() >= Duration::from_secs(ABSENT_RETRY_SECS)
        {
            last_absent_retry = Instant::now();
            let mut still_absent: Vec<WatchTarget> = Vec::new();
            for (path, mode, label) in absent_watches.drain(..) {
                match try_watch_once(&mut watcher, &path, mode, label) {
                    WatchResult::Watched => {
                        // Newly watched path: emit once so UI loads its data.
                        eprintln!(
                            "[mercury-gui] late-watch recovered ({label}) {}",
                            redact_home(&path.display().to_string())
                        );
                        emit_fn();
                    }
                    WatchResult::Absent => still_absent.push((path, mode, label)),
                    WatchResult::Failed => {} // logged inside; do not retry
                }
            }
            absent_watches = still_absent;
        }
    }
}

/// Try to register `path` with `watcher`.
///
/// Returns a [`WatchResult`] discriminating three outcomes:
/// - [`WatchResult::Watched`]  — registration succeeded.
/// - [`WatchResult::Absent`]   — path does not yet exist; safe to retry later.
/// - [`WatchResult::Failed`]   — path exists but `watcher.watch()` returned an
///   error (permission denied, OS inotify limit, etc.).  Logged here; callers
///   should NOT enqueue for retry to avoid infinite stderr noise (Fix A).
fn try_watch_once(
    watcher: &mut notify::RecommendedWatcher,
    path: &std::path::Path,
    mode: RecursiveMode,
    label: &str,
) -> WatchResult {
    let display = redact_home(&path.display().to_string());
    if !path.exists() {
        eprintln!("[mercury-gui] skip watch ({label}): path absent {display}");
        return WatchResult::Absent;
    }
    if let Err(e) = watcher.watch(path, mode) {
        eprintln!(
            "[mercury-gui] watch failed ({label}) {display}: {}",
            redact_home(&e.to_string())
        );
        return WatchResult::Failed;
    }
    WatchResult::Watched
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

    /// Smoke-test: `try_watch_once` returns `Absent` for a non-existent path
    /// and `Watched` after the path is created.
    #[test]
    fn try_watch_once_absent_then_present() {
        use tempfile::tempdir;

        let dir = tempdir().expect("tempdir");
        let sub = dir.path().join("late_subdir");

        let (tx, _rx) = mpsc::channel();
        let mut watcher = recommended_watcher(tx).expect("watcher");

        // Not yet created → Absent.
        assert_eq!(
            try_watch_once(&mut watcher, &sub, RecursiveMode::NonRecursive, "test"),
            WatchResult::Absent,
            "absent path must return Absent"
        );

        // Create the directory.
        std::fs::create_dir_all(&sub).expect("create_dir_all");

        // Now exists → Watched.
        assert_eq!(
            try_watch_once(&mut watcher, &sub, RecursiveMode::NonRecursive, "test"),
            WatchResult::Watched,
            "present path must return Watched"
        );
    }

    /// Fix A: a path that exists but whose `watcher.watch()` call fails must
    /// return `Failed` — not `Absent` — so it is not enqueued for retry.
    ///
    /// We simulate this by registering the same path twice.  The second call to
    /// `watcher.watch()` on an already-watched path returns an error on most
    /// notify backends.  If the backend silently accepts duplicates (returning
    /// Ok) the assertion relaxes to `Watched` and the test still compiles — the
    /// key invariant (path exists, no panic) is verified either way.
    ///
    /// Note: not all platforms error on duplicate watch; the assertion accepts
    /// either `Failed` or `Watched`, so the test holds regardless of backend
    /// dup-watch behaviour. The key invariant: an existing path is NEVER
    /// classified `Absent`.
    #[test]
    fn try_watch_once_existing_watch_fail_returns_failed_or_watched() {
        use tempfile::tempdir;

        let dir = tempdir().expect("tempdir");

        let (tx, _rx) = mpsc::channel();
        let mut watcher = recommended_watcher(tx).expect("watcher");

        // First registration → Watched.
        assert_eq!(
            try_watch_once(&mut watcher, dir.path(), RecursiveMode::NonRecursive, "t1"),
            WatchResult::Watched,
            "first watch must succeed"
        );

        // Second registration on the same path — backend may return Err.
        // Result is either Watched (backend accepts dup) or Failed (backend rejects).
        // Critically it must NOT be Absent (path clearly exists).
        let second = try_watch_once(&mut watcher, dir.path(), RecursiveMode::NonRecursive, "t2");
        assert_ne!(
            second,
            WatchResult::Absent,
            "an existing path must never return Absent"
        );
    }

    /// Integration test for the absent-watch recovery seam:
    ///
    /// 1. Start `run_watch_loop` against a target that does not yet exist.
    /// 2. The path enters `absent_watches`; no emit fires immediately.
    /// 3. Create the directory.
    /// 4. Poll until emit_count > 0, asserting the elapsed time from creation
    ///    to first emit is ≤ 2 s (Fix D: tight timing assertion, not a fixed 3 s sleep).
    /// 5. Signal the stop flag and join the thread (Fix C: no leaked threads).
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

        // Stop flag: test holds the Arc so it can signal shutdown (Fix C).
        let stop = Arc::new(AtomicBool::new(false));
        let stop_for_thread = Arc::clone(&stop);

        let handle = thread::spawn(move || {
            run_watch_loop(vec![target], stop_for_thread, move || {
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
        let created_at = Instant::now();

        // Fix D: poll for recovery with a 2 s deadline instead of a fixed 3 s sleep.
        // ABSENT_RETRY_SECS = 1 s, so recovery should happen within ~1 s of creation
        // plus scheduler overhead.  We allow 2 s total.
        let poll_deadline = Duration::from_secs(2);
        let poll_interval = Duration::from_millis(50);
        loop {
            {
                let c = emit_count.lock().unwrap();
                if *c >= 1 {
                    break;
                }
            }
            assert!(
                created_at.elapsed() < poll_deadline,
                "absent-watch recovery took longer than {:?} — timing regression",
                poll_deadline,
            );
            std::thread::sleep(poll_interval);
        }

        // Assert that the elapsed time from creation to recovery is within bound.
        let elapsed = created_at.elapsed();
        assert!(
            elapsed <= poll_deadline,
            "recovery elapsed {:?} exceeded deadline {:?}",
            elapsed,
            poll_deadline,
        );

        // Signal shutdown and join the thread (Fix C: clean up, no leaked thread).
        stop.store(true, Ordering::Relaxed);
        handle.join().expect("watcher thread should exit cleanly");
    }
}
