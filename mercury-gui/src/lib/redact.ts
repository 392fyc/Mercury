// Defense-in-depth recursive home-path redaction.
//
// Mercury's primary PII contract is Rust-side: `JobState::sanitize_paths`
// scrubs `cwd` / `linkScanPath` / `worktreePath` before serializing. But the
// Rust side does NOT recursively scrub `output` / `children` (untyped JSON
// blobs that may contain LLM-emitted home paths) or any future string field.
//
// This module is the second surface — applied at the frontend boundary right
// before user-visible JSON rendering. Mirrors the Rust `redact_home` regex:
// Windows `C:\Users\<name>\...`, POSIX `/home/<name>/...`, macOS
// `/Users/<name>/...` → `~`.

const PATTERNS: readonly RegExp[] = [
  /[A-Z]:[\\/]Users[\\/][^\\/"]+/gi,
  /\/home\/[^/"]+/g,
  /\/Users\/[^/"]+/g,
];

function redactString(s: string): string {
  let out = s;
  for (const re of PATTERNS) out = out.replace(re, "~");
  return out;
}

export function redactHomePaths(value: unknown): unknown {
  if (typeof value === "string") return redactString(value);
  if (Array.isArray(value)) return value.map(redactHomePaths);
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] = redactHomePaths(v);
    }
    return out;
  }
  return value;
}
