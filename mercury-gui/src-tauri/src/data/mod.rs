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

/// Cached `(home_str, alt_form)` for the current process.
///
/// Only **successful** lookups are cached. A transient lookup failure (env
/// var unavailable, mid-init filesystem state, etc.) returns `None` but does
/// NOT poison the cache — subsequent calls will retry `dirs::home_dir()`.
/// This avoids the silent-permanent-disable hazard where one bad early call
/// would leak home paths for the rest of the process lifetime.
fn home_forms() -> Option<&'static (String, String)> {
    static CACHED: std::sync::OnceLock<(String, String)> = std::sync::OnceLock::new();
    if let Some(v) = CACHED.get() {
        return Some(v);
    }
    // Cache empty → compute and cache only if successful.
    let home = dirs::home_dir()?;
    let home_str = home.to_str()?.to_string();
    if home_str.is_empty() {
        return None;
    }
    let alt = home_str.replace('\\', "/");
    // Race-tolerant: if a parallel thread populates first, `get_or_init`
    // returns the existing value; our (home_str, alt) is dropped harmlessly.
    Some(CACHED.get_or_init(|| (home_str, alt)))
}

/// Replace the resolved home directory with `~` so paths surfaced to the
/// frontend or stderr never include `C:\Users\<name>\...` literals.
/// Handles both backslash and forward-slash variants for cross-platform safety.
///
/// Performance note: the expensive `String::replace` call path (which allocates
/// a new buffer scanning + replacing) is only taken when the input actually
/// contains a home-shaped substring. On the non-match common case the function
/// still allocates a single owned copy of the input (one `s.to_string()`) to
/// satisfy the `String` return type — there is no zero-alloc path. For true
/// zero-alloc non-match behavior, callers should use the contains-check pattern
/// in `redact_home_in_json_at` (see below).
pub fn redact_home(s: &str) -> String {
    let Some((home_str, alt)) = home_forms() else {
        return s.to_string();
    };
    let home_s: &str = home_str.as_str();
    let alt_s: &str = alt.as_str();
    let has_home = s.contains(home_s);
    let has_alt = alt_s != home_s && s.contains(alt_s);
    if !has_home && !has_alt {
        return s.to_string();
    }
    let mut out = if has_home { s.replace(home_s, "~") } else { s.to_string() };
    if has_alt {
        out = out.replace(alt_s, "~");
    }
    out
}

/// Maximum recursion depth for `redact_home_in_json`. Matches the
/// `serde_json::de::Deserializer` default recursion limit so any JSON the
/// watcher could have parsed in via `serde_json::from_*` is safely walkable.
/// LLM-emitted `output` / `children` blobs that exceed this depth are
/// truncated at this depth (the over-depth subtree is left unscrubbed but
/// the call does not panic / overflow the stack).
const REDACT_JSON_MAX_DEPTH: u16 = 128;

/// Recursively scrub home paths from a JSON value in place.
///
/// Walks `Value::String` (redact + replace), `Value::Array` (each element), and
/// `Value::Object` (each entry's value); passes through `Number / Bool / Null`
/// unchanged. Used for `JobState::output` and `JobState::children`, which are
/// untyped `serde_json::Value` blobs that may carry LLM-emitted home paths.
///
/// Idempotent on already-scrubbed input (running twice produces no further change).
///
/// Depth-bounded by `REDACT_JSON_MAX_DEPTH` to avoid stack overflow on
/// adversarial / malformed deeply-nested input; past that depth, the over-depth
/// subtree is left as-is rather than panicking.
pub fn redact_home_in_json(value: &mut serde_json::Value) {
    redact_home_in_json_at(value, 0);
}

fn redact_home_in_json_at(value: &mut serde_json::Value, depth: u16) {
    if depth >= REDACT_JSON_MAX_DEPTH {
        return;
    }
    match value {
        serde_json::Value::String(s) => {
            // Fast path: skip the redact_home call entirely if the string can't
            // contain a home-shape. Avoids the `s.to_string()` clone that
            // `redact_home` would otherwise return for the common non-match
            // case on the IPC hot path.
            let needs_scrub = match home_forms() {
                Some((home_str, alt)) => s.contains(home_str.as_str()) || s.contains(alt.as_str()),
                None => false,
            };
            if needs_scrub {
                let redacted = redact_home(s);
                if redacted != *s {
                    *s = redacted;
                }
            }
        }
        serde_json::Value::Array(arr) => {
            for v in arr.iter_mut() {
                redact_home_in_json_at(v, depth + 1);
            }
        }
        serde_json::Value::Object(obj) => {
            for (_, v) in obj.iter_mut() {
                redact_home_in_json_at(v, depth + 1);
            }
        }
        // Number / Bool / Null — no scrubbing needed.
        _ => {}
    }
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

    fn home_str_for_test() -> Option<String> {
        dirs::home_dir().and_then(|p| p.to_str().map(String::from))
    }

    #[test]
    fn redact_home_in_json_walks_nested_object() {
        let Some(home) = home_str_for_test() else {
            return; // no home dir — nothing to test
        };
        let mut v = serde_json::json!({
            "result": format!("{home}/.claude/jobs/abc/state.json"),
            "meta": { "log_path": format!("{home}/.claude/log.txt") }
        });
        redact_home_in_json(&mut v);
        let serialized = v.to_string();
        assert!(!serialized.contains(&home), "home must be scrubbed at every depth");
        assert!(serialized.contains("~/.claude/jobs/"));
        assert!(serialized.contains("~/.claude/log.txt"));
    }

    #[test]
    fn redact_home_in_json_walks_nested_array() {
        let Some(home) = home_str_for_test() else {
            return;
        };
        let mut v = serde_json::json!([
            format!("{home}/.claude/a"),
            format!("{home}/.claude/b"),
            "no home here"
        ]);
        redact_home_in_json(&mut v);
        let serialized = v.to_string();
        assert!(!serialized.contains(&home));
        assert!(serialized.contains("~/.claude/a"));
        assert!(serialized.contains("~/.claude/b"));
        assert!(serialized.contains("no home here"));
    }

    #[test]
    fn redact_home_in_json_walks_mixed_nesting() {
        let Some(home) = home_str_for_test() else {
            return;
        };
        // Object → Array → Object → String  (3-deep mixed nesting)
        let mut v = serde_json::json!({
            "tasks": [
                { "cwd": format!("{home}/proj/a") },
                { "cwd": format!("{home}/proj/b"), "child": { "log": format!("{home}/log.txt") } }
            ]
        });
        redact_home_in_json(&mut v);
        let serialized = v.to_string();
        assert!(!serialized.contains(&home), "deep nesting must be walked");
        assert!(serialized.contains("~/proj/a"));
        assert!(serialized.contains("~/proj/b"));
        assert!(serialized.contains("~/log.txt"));
    }

    #[test]
    fn redact_home_in_json_idempotent() {
        let Some(home) = home_str_for_test() else {
            return;
        };
        let mut v = serde_json::json!({ "path": format!("{home}/.claude/x") });
        redact_home_in_json(&mut v);
        let first = v.clone();
        redact_home_in_json(&mut v); // second call should produce no further change
        assert_eq!(first, v, "redact_home_in_json must be idempotent");
    }

    #[test]
    fn redact_home_in_json_noop_on_non_string() {
        // Numbers / bools / null must pass through unchanged.
        let mut nums = serde_json::json!(42);
        redact_home_in_json(&mut nums);
        assert_eq!(nums, serde_json::json!(42));

        let mut b = serde_json::json!(true);
        redact_home_in_json(&mut b);
        assert_eq!(b, serde_json::json!(true));

        let mut nil = serde_json::Value::Null;
        redact_home_in_json(&mut nil);
        assert_eq!(nil, serde_json::Value::Null);
    }

    #[test]
    fn redact_home_in_json_scrubs_root_string() {
        // Value::String at the JSON root (not nested inside an object/array).
        let Some(home) = home_str_for_test() else {
            return;
        };
        let mut v = serde_json::Value::String(format!("{home}/.claude/x"));
        redact_home_in_json(&mut v);
        match &v {
            serde_json::Value::String(s) => {
                assert!(!s.contains(&home), "root string must be scrubbed");
                assert!(s.contains("~/.claude/x"));
            }
            _ => panic!("expected Value::String after redaction"),
        }
    }

    #[test]
    fn redact_home_in_json_depth_bound_does_not_panic() {
        // Build a nested object that exceeds REDACT_JSON_MAX_DEPTH (128).
        // 200 is large enough to prove the depth-bound branch is reached, and
        // small enough that serde_json::Value's recursive Drop at end-of-test
        // does not blow the default test-thread stack (~2MB).
        let mut v = serde_json::Value::Null;
        for _ in 0..200 {
            v = serde_json::json!({ "n": v });
        }
        redact_home_in_json(&mut v); // must not panic / stack-overflow
        // Sanity: the value is still navigable (we don't assert internal scrub state past the depth bound).
        assert!(v.is_object());
    }

    #[test]
    fn redact_home_in_json_depth_bound_actually_short_circuits() {
        // Stronger guarantee than the no-panic test: prove the depth cutoff
        // leaves over-depth subtrees unscrubbed (i.e., the bound is doing real
        // work, not just decorative). A home path placed at depth 200 (well
        // past REDACT_JSON_MAX_DEPTH = 128) must survive the call unchanged;
        // a shallow home path at depth 0 must be scrubbed normally.
        let Some(home) = home_str_for_test() else {
            return;
        };
        let leaf = serde_json::Value::String(format!("{home}/deep-path"));
        // Build 200 layers of `{"n": <child>}` around the leaf.
        let mut deep = leaf;
        for _ in 0..200 {
            deep = serde_json::json!({ "n": deep });
        }
        // Wrap so the test fixture also has a shallow home path at root.
        let mut v = serde_json::json!({
            "shallow": format!("{home}/shallow"),
            "deep_tree": deep,
        });
        redact_home_in_json(&mut v);
        // Shallow must be scrubbed.
        let serialized_shallow = v["shallow"].to_string();
        assert!(!serialized_shallow.contains(&home), "shallow path must be scrubbed");
        // Find the leaf 200 levels deep — should still contain the unredacted home.
        let mut cursor = &v["deep_tree"];
        for _ in 0..200 {
            cursor = &cursor["n"];
        }
        let leaf_str = cursor.as_str().expect("leaf is a String");
        assert!(
            leaf_str.contains(&home),
            "depth bound must short-circuit past REDACT_JSON_MAX_DEPTH; leaf at depth 200 should remain unscrubbed"
        );
    }

    #[test]
    fn redact_home_in_json_empty_collections_safe() {
        let mut arr = serde_json::json!([]);
        redact_home_in_json(&mut arr);
        assert_eq!(arr, serde_json::json!([]));

        let mut obj = serde_json::json!({});
        redact_home_in_json(&mut obj);
        assert_eq!(obj, serde_json::json!({}));
    }
}
