'use strict';

// Router IPC bootstrap — token cache, routerFetch, ensureRouter,
// register/deregister. #303 split from channel.cjs.

const { spawn } = require('child_process');
const fs = require('fs');
const state = require('./state.cjs');
const { routerFetchWithRetry } = require('./token-refresh.cjs');

const { PORT, ROUTER_CJS, TOKEN_FILE, TAG, SESSION_ID, SESSION_SHORT, PROJECT_PATH } = state;

// Issue #304: extracted magic numbers near their use sites.
const ROUTER_HEALTH_PROBE_TIMEOUT_MS = 500;  // ensureRouter() /health probe AbortSignal
const ROUTER_START_RETRY_ATTEMPTS    = 20;   // ensureRouter() spawn-then-probe attempts
const ROUTER_START_RETRY_INTERVAL_MS = 250;  // ensureRouter() inter-probe sleep

// IPC token reader (with retry for router startup race).
async function readToken(retries = 5, delayMs = 200) {
  for (let i = 0; i < retries; i++) {
    try { return fs.readFileSync(TOKEN_FILE, 'utf8').trim(); } catch {}
    if (i < retries - 1) await new Promise(r => setTimeout(r, delayMs));
  }
  return null;
}

// Token cache — resolved once after router starts.
// Issue #301: invalidateToken() clears the cache so routerFetchWithRetry can
// re-read after a router restart (token-file rewrite). Without this, a router
// restart would silently 401 every subsequent IPC call.
async function getToken() {
  if (state._token) return state._token;
  state._token = await readToken();
  return state._token;
}
function invalidateToken() { state._token = null; }

// Issue #304 nit 3: parameter named `endpoint` so it no longer shadows the
// `path` module import; callers pass the IPC route segment.
async function routerFetch(endpoint, opts = {}) {
  const url = `http://127.0.0.1:${PORT}${endpoint}`;
  return routerFetchWithRetry({ url, opts, getToken, invalidateToken, fetchImpl: fetch });
}

async function ensureRouter() {
  try {
    const r = await fetch(`http://127.0.0.1:${PORT}/health`, { signal: AbortSignal.timeout(ROUTER_HEALTH_PROBE_TIMEOUT_MS) });
    if (r.ok) return;
  } catch {}
  spawn('node', [ROUTER_CJS], { detached: true, stdio: 'ignore', windowsHide: true }).unref();
  for (let i = 0; i < ROUTER_START_RETRY_ATTEMPTS; i++) {
    await new Promise(r => setTimeout(r, ROUTER_START_RETRY_INTERVAL_MS));
    try { const r = await fetch(`http://127.0.0.1:${PORT}/health`); if (r.ok) return; } catch {}
  }
  throw new Error(`router did not start within ${ROUTER_START_RETRY_ATTEMPTS * ROUTER_START_RETRY_INTERVAL_MS}ms`);
}

async function register() {
  const res = await routerFetch('/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: SESSION_ID, project_path: PROJECT_PATH, branch: state.branch, pid: process.pid, short_id: SESSION_SHORT }),
  });
  if (res.status === 429) { process.stderr.write(`${TAG} session limit reached; Telegram inactive\n`); return false; }
  if (!res.ok) { process.stderr.write(`${TAG} register failed: HTTP ${res.status}\n`); return false; }
  return true;
}

async function deregister() {
  try { await routerFetch(`/register/${SESSION_ID}`, { method: 'DELETE' }); } catch {}
}

module.exports = {
  readToken, getToken, invalidateToken, routerFetch, ensureRouter, register, deregister,
  ROUTER_HEALTH_PROBE_TIMEOUT_MS, ROUTER_START_RETRY_ATTEMPTS, ROUTER_START_RETRY_INTERVAL_MS,
};
