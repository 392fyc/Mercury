#!/usr/bin/env node
'use strict';

// Mercury Notify — thin HTTP client forwarding notifications to mercury-channel-router.
// Callers: skills/hooks emitting USER-ACTIONABLE events only — Dev Pipeline complete,
// handoff session-switch, permission relay, critical security. Internal agent state
// (loop-detector stalls, hook failures, autocompact, heartbeat) MUST NOT call this;
// see adapters/mercury-channel-router/README.md "Acceptable Callers" + Issue #316.
// Never throws; always returns {ok, ...}.

const fs   = require('fs');
const os   = require('os');
const path = require('path');

const PORT       = process.env.MERCURY_ROUTER_PORT || 8788;
const TOKEN_FILE = path.join(os.homedir(), '.mercury', 'router.token');

function readToken() {
  try { return fs.readFileSync(TOKEN_FILE, 'utf8').trim(); } catch { return null; }
}

async function notify(severity, title, body, options = {}) {
  if (process.env.MERCURY_NOTIFY_DISABLED) return { ok: true, skipped: true };
  const token = readToken();
  if (!token) return { ok: false, error: 'no_token' };
  try {
    const res = await fetch(`http://127.0.0.1:${PORT}/notify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
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
