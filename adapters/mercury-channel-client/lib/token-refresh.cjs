'use strict';

// IPC token cache invalidation on 401 (Issue #301, security tier).
//
// channel.cjs caches the router IPC bearer token in `_token` after the first
// successful read of `~/.mercury/router.token`. If the router process
// restarts (token regenerated and rewritten), the cached token becomes stale
// and EVERY subsequent routerFetch returns 401 silently — the session keeps
// running but no Telegram traffic flows. Source: PR #295 Copilot iter 5.
//
// Contract: routerFetchWithRetry attempts the request with the currently
// cached token. On HTTP 401, it invokes `invalidateToken()` to clear the
// cache, then retries the request ONCE with a fresh `getToken()` read.
//
// Retry-once cap is deliberate: a token-file write race or a router-side
// auth bug must not turn a single 401 into an unbounded loop. The second
// 401 propagates to the caller (returned, not thrown).

async function routerFetchWithRetry({
  url,
  opts = {},
  getToken,
  invalidateToken,
  fetchImpl,
  timeoutMs = 3000,
}) {
  const attempt = async () => {
    const token = await getToken();
    const headers = { ...(opts.headers || {}) };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    // Caller-supplied opts.signal still wins (existing semantics) — the
    // explicit timeout is the floor, not a ceiling.
    return fetchImpl(url, { signal: AbortSignal.timeout(timeoutMs), ...opts, headers });
  };
  const res = await attempt();
  if (res.status !== 401) return res;
  await invalidateToken();
  return attempt();
}

module.exports = { routerFetchWithRetry };
