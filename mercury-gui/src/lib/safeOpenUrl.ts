// safeOpenUrl — defense-in-depth wrapper around tauri-plugin-opener (#416).
// Whitelists https://github.com and https://www.github.com hosts so a future
// data-integrity gap (compromised gh CLI, JSON injection, IPC tampering)
// cannot redirect a user click to an arbitrary URL. Tauri's default opener
// regex already restricts to http(s)/mailto/tel; this is the second layer.

import { openUrl } from "@tauri-apps/plugin-opener";

const ALLOWED_HOSTS = new Set(["github.com", "www.github.com"]);

export function safeOpenUrl(raw: string): Promise<void> {
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    return Promise.reject(new Error(`safeOpenUrl: malformed URL ${raw}`));
  }
  if (parsed.protocol !== "https:") {
    return Promise.reject(
      new Error(`safeOpenUrl: rejected non-https URL ${raw}`)
    );
  }
  if (!ALLOWED_HOSTS.has(parsed.host)) {
    return Promise.reject(
      new Error(`safeOpenUrl: rejected host ${parsed.host}`)
    );
  }
  return openUrl(parsed.toString());
}
