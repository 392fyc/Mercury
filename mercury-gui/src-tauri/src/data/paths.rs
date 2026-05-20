use std::path::PathBuf;

/// Resolve Claude's config dir.
///
/// Precedence: `$CLAUDE_CONFIG_DIR` env var → `dirs::home_dir().join(".claude")`.
/// Mercury convention; do NOT hardcode any user path.
pub fn claude_config_dir() -> PathBuf {
    if let Ok(s) = std::env::var("CLAUDE_CONFIG_DIR") {
        return PathBuf::from(s);
    }
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".claude")
}

pub fn jobs_dir() -> PathBuf {
    claude_config_dir().join("jobs")
}

pub fn roster_path() -> PathBuf {
    claude_config_dir().join("daemon").join("roster.json")
}

/// Mercury LANES.md canonical path per `scripts/lane-assertion.sh:128-131`.
///
/// Precedence: `$MERCURY_MEMORY_DIR` env override → `<claude_config_dir>/projects/D--Mercury-Mercury/memory/`.
/// User-memory file, NOT a repo file.
pub fn lanes_path() -> PathBuf {
    if let Ok(s) = std::env::var("MERCURY_MEMORY_DIR") {
        return PathBuf::from(s).join("LANES.md");
    }
    claude_config_dir()
        .join("projects")
        .join("D--Mercury-Mercury")
        .join("memory")
        .join("LANES.md")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    // Env-var mutation is process-global; cargo's default test-threads = num_cpus.
    // Serialize all env-touching tests under a single mutex to prevent interleaving.
    static ENV_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn claude_config_dir_respects_env() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var("CLAUDE_CONFIG_DIR", "/tmp/fake-claude");
        assert_eq!(claude_config_dir(), PathBuf::from("/tmp/fake-claude"));
        std::env::remove_var("CLAUDE_CONFIG_DIR");
    }

    #[test]
    fn jobs_dir_under_config_dir() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var("CLAUDE_CONFIG_DIR", "/tmp/cc-jobs");
        assert_eq!(jobs_dir(), PathBuf::from("/tmp/cc-jobs/jobs"));
        std::env::remove_var("CLAUDE_CONFIG_DIR");
    }

    #[test]
    fn roster_path_under_daemon() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var("CLAUDE_CONFIG_DIR", "/tmp/cc-roster");
        assert_eq!(
            roster_path(),
            PathBuf::from("/tmp/cc-roster/daemon/roster.json")
        );
        std::env::remove_var("CLAUDE_CONFIG_DIR");
    }

    #[test]
    fn lanes_path_respects_mercury_env() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var("MERCURY_MEMORY_DIR", "/tmp/mem");
        assert_eq!(lanes_path(), PathBuf::from("/tmp/mem/LANES.md"));
        std::env::remove_var("MERCURY_MEMORY_DIR");
    }
}
