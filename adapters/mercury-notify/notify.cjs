#!/usr/bin/env node
'use strict';

// Mercury Notify — thin HTTP client forwarding notifications to mercury-channel-router.
// Callers: hook scripts (loop-detector, post-commit, etc.).
// Never throws; always returns {ok, ...}.

const PORT = process.env.MERCURY_ROUTER_PORT || 8788;

async function notify(severity, title, body, options = {}) {
  if (process.env.MERCURY_NOTIFY_DISABLED) return { ok: true, skipped: true };
  try {
    const res = await fetch(`http://127.0.0.1:${PORT}/notify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ severity, title, body, ...options }),
      signal: AbortSignal.timeout(2000),
    });
    if (!res.ok) return { ok: false, error: `router_${res.status}` };
    return { ok: true };
  } catch (e) {
    process.stderr.write(`[mercury-notify] ${e.message}\n`);
    return { ok: false, error: 'transport' };
  }
}

module.exports = { notify };
