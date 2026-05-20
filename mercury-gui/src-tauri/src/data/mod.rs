pub mod commands;
pub mod models;
pub mod paths;
pub mod watcher;

use thiserror::Error;

#[derive(Debug, Error)]
pub enum DataError {
    #[error("io: {0}")]
    Io(String),
    #[error("parse: {0}")]
    Parse(String),
}

impl serde::Serialize for DataError {
    fn serialize<S: serde::Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        s.serialize_str(&self.to_string())
    }
}

impl From<std::io::Error> for DataError {
    fn from(e: std::io::Error) -> Self {
        DataError::Io(redact_home(&e.to_string()))
    }
}

impl From<serde_json::Error> for DataError {
    fn from(e: serde_json::Error) -> Self {
        DataError::Parse(redact_home(&e.to_string()))
    }
}

/// Replace the resolved home directory with `~` so paths surfaced to the
/// frontend or stderr never include `C:\Users\<name>\...` literals.
/// Handles both backslash and forward-slash variants for cross-platform safety.
pub fn redact_home(s: &str) -> String {
    let Some(home) = dirs::home_dir() else {
        return s.to_string();
    };
    let Some(home_str) = home.to_str() else {
        return s.to_string();
    };
    if home_str.is_empty() {
        return s.to_string();
    }
    let alt = home_str.replace('\\', "/");
    let mut out = s.replace(home_str, "~");
    if alt != home_str {
        out = out.replace(&alt, "~");
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redact_home_replaces_home_prefix() {
        let Some(home) = dirs::home_dir().and_then(|p| p.to_str().map(String::from)) else {
            return;
        };
        let leaky = format!("read {home}/.claude/jobs/abc/state.json failed");
        let redacted = redact_home(&leaky);
        assert!(!redacted.contains(&home), "home path must be scrubbed");
        assert!(redacted.contains("~/.claude/jobs/"));
    }

    #[test]
    fn redact_home_handles_string_without_home() {
        assert_eq!(redact_home("plain message"), "plain message");
    }
}
