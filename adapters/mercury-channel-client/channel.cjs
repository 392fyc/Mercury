#!/usr/bin/env node
'use strict';

// Mercury Channel Client — MCP server bridging Claude Code sessions to the channel router.
// Loaded by Claude Code via .mcp.json; one instance per session.

const { spawn }  = require('child_process');
const { Server } = require('@modelcontextprotocol/sdk/server/index.js');
const { StdioServerTransport } = require('@modelcontextprotocol/sdk/server/stdio.js');
const { CallToolRequestSchema, ListToolsRequestSchema } = require('@modelcontextprotocol/sdk/types.js');
const crypto = require('crypto');
const path   = require('path');
const fs     = require('fs');
const os     = require('os');
const { detectBranchSync, detectBranchAsync } = require('./lib/detect-branch.cjs');
const { nextBackoffMs, isSubstantiveEvent } = require('./lib/backoff.cjs');
const { routerFetchWithRetry } = require('./lib/token-refresh.cjs');

const PORT       = Number(process.env.MERCURY_ROUTER_PORT) || 8788;
const ROUTER_CJS = path.join(__dirname, '..', 'mercury-channel-router', 'router.cjs');
const TOKEN_FILE = path.join(os.homedir(), '.mercury', 'router.token');
const TAG        = '[mercury-channel-client]';
const xmlEsc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&apos;');

// ── Session identity ──────────────────────────────────────────────────────────
const SESSION_ID = process.env.CLAUDE_SESSION_ID || `cc-${process.pid}-${Date.now().toString(36)}`;
// Issue #297: branch detection deferred to async path so module load is not
// blocked by `git branch --show-current` (was 50-200ms typical, longer if git
// hung). Initial value comes from MERCURY_BRANCH_OVERRIDE env var or 'unknown';
// async git lookup runs after mcp.connect() and re-registers on resolution.
let   branch     = detectBranchSync();

const PROJECT_PATH  = process.env.CLAUDE_PROJECT_DIR || process.cwd();
// ADR §7.6: 6-char prefix = first 6 hex chars of sha1(SESSION_ID)
const SESSION_SHORT = crypto.createHash('sha1').update(SESSION_ID).digest('hex').slice(0,6);

// ── IPC token reader (with retry for router startup race) ────────────────────
async function readToken(retries = 5, delayMs = 200) {
  for (let i = 0; i < retries; i++) {
    try { return fs.readFileSync(TOKEN_FILE, 'utf8').trim(); } catch {}
    if (i < retries - 1) await new Promise(r => setTimeout(r, delayMs));
  }
  return null;
}

// token cache — resolved once after router starts.
// Issue #301: invalidateToken() clears the cache so routerFetchWithRetry can
// re-read after a router restart (token-file rewrite). Without this, a router
// restart would silently 401 every subsequent IPC call.
let _token = null;
async function getToken() {
  if (_token) return _token;
  _token = await readToken();
  return _token;
}
function invalidateToken() { _token = null; }

// ── Router IPC helpers ────────────────────────────────────────────────────────
async function routerFetch(path_, opts = {}) {
  const url = `http://127.0.0.1:${PORT}${path_}`;
  return routerFetchWithRetry({ url, opts, getToken, invalidateToken, fetchImpl: fetch });
}

async function ensureRouter() {
  try {
    const r = await fetch(`http://127.0.0.1:${PORT}/health`, { signal: AbortSignal.timeout(500) });
    if (r.ok) return;
  } catch {}
  spawn('node', [ROUTER_CJS], { detached: true, stdio: 'ignore', windowsHide: true }).unref();
  for (let i = 0; i < 20; i++) {
    await new Promise(r => setTimeout(r, 250));
    try { const r = await fetch(`http://127.0.0.1:${PORT}/health`); if (r.ok) return; } catch {}
  }
  throw new Error('router did not start within 5s');
}

async function register() {
  const res = await routerFetch('/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: SESSION_ID, project_path: PROJECT_PATH, branch, pid: process.pid, short_id: SESSION_SHORT }),
  });
  if (res.status === 429) { process.stderr.write(`${TAG} session limit reached; Telegram inactive\n`); return false; }
  if (!res.ok) { process.stderr.write(`${TAG} register failed: HTTP ${res.status}\n`); return false; }
  return true;
}

async function deregister() {
  try { await routerFetch(`/register/${SESSION_ID}`, { method: 'DELETE' }); } catch {}
}

// ── MCP server ────────────────────────────────────────────────────────────────
const mcp = new Server(
  { name: 'mercury-telegram', version: '0.1.0' },
  {
    capabilities: {
      experimental: { 'claude/channel': {}, 'claude/channel/permission': {} },
      tools: {},
    },
    instructions:
      'Telegram messages arrive as <channel source="mercury-telegram" label="..."> tags. ' +
      'Use the reply tool to respond, passing chat_id from the tag.',
  }
);

// ── Tool: reply ───────────────────────────────────────────────────────────────
mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [{
    name: 'reply',
    description: 'Send a reply to Telegram via the channel router.',
    inputSchema: {
      type: 'object',
      properties: {
        chat_id: { type: 'number', description: 'Telegram chat_id from the channel tag' },
        text:    { type: 'string', description: 'Message text (HTML allowed)' },
      },
      required: ['chat_id', 'text'],
    },
  }],
}));

mcp.setRequestHandler(CallToolRequestSchema, async (req) => {
  if (req.params.name !== 'reply') throw new Error(`Unknown tool: ${req.params.name}`);
  const { chat_id, text } = req.params.arguments || {};
  try {
    await routerFetch('/reply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id, text, session_id: SESSION_ID }),
    });
    // take ownership after responding
    routerFetch(`/take-ownership/${SESSION_ID}`, { method: 'POST' }).catch(() => {});
    return { content: [{ type: 'text', text: 'reply sent' }] };
  } catch (e) {
    return { content: [{ type: 'text', text: `reply failed: ${e.message}` }], isError: true };
  }
});

// ── Outbound permission_request relay (ADR §5.2 step 6 + §7.6) ───────────────
mcp.fallbackNotificationHandler = async (notification) => {
  if (notification.method !== 'notifications/claude/channel/permission_request') return;
  const { tool_name = '', description = '', input_preview = '', request_id = '' } = notification.params || {};
  const prefixed = `${SESSION_SHORT}-${request_id}`;
  try {
    await routerFetch('/permission-request', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: SESSION_ID, tool_name, description, input_preview, prefixed_request_id: prefixed }),
    });
  } catch (e) { process.stderr.write(`${TAG} permission-request relay failed: ${e.message}\n`); }
};

// ── SSE inbox consumer ────────────────────────────────────────────────────────
let sseActive = true;
// Issue #297 R3-M1: track whether connectInbox() has been wired. The initial
// register() path may fail (429 cap, transient) but the deferred async
// re-register may later succeed; without this flag the late-success path
// would leave the session registered at the router with no SSE consumer,
// silently dropping lane-targeted messages.
let inboxStarted = false;
function startInboxIfNeeded() {
  if (inboxStarted) return;
  inboxStarted = true;
  connectInbox().catch(e => process.stderr.write(`${TAG} inbox error: ${e.message}\n`));
}

async function connectInbox() {
  // Issue #299: exponential reconnect backoff (1s → 2s → 5s → 10s → 30s max).
  // `reconnectAttempt` is the count of consecutive *failed-or-empty*
  // connections; a connection that delivers at least one **substantive**
  // event (message/verdict/command, NOT the synthetic `connected` ping the
  // router sends on every attach) resets it so transient flaps don't
  // accumulate into a 30s wait.
  let reconnectAttempt = 0;
  while (sseActive) {
    let receivedEvent = false;
    try {
      const token = await getToken();
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      // no AbortSignal timeout: SSE is indefinite-lived; reconnect only on real disconnect
      const res = await fetch(`http://127.0.0.1:${PORT}/inbox/${SESSION_ID}`, { headers });
      if (!res.ok || !res.body) {
        // Issue #301: SSE goes through a bare fetch, not routerFetchWithRetry,
        // so 401 here (router restart → stale token) would otherwise loop
        // forever on the same cached token. Invalidate so the NEXT iteration's
        // getToken() re-reads ~/.mercury/router.token. The retry-once contract
        // still holds: caller can only walk the backoff schedule (1s..30s) for
        // a sustained 401 stream, not amplify it.
        if (res.status === 401) invalidateToken();
        await new Promise(r => setTimeout(r, nextBackoffMs(reconnectAttempt++)));
        continue;
      }
      const reader = res.body.getReader();
      const dec    = new TextDecoder();
      let   buf    = '';
      while (sseActive) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split('\n\n');
        buf = parts.pop();
        for (const part of parts) {
          const line = part.split('\n').find(l => l.startsWith('data:'));
          if (!line) continue;
          try {
            const evt = JSON.parse(line.slice(5).trim());
            // Reset backoff on first substantive event. Excludes the router's
            // synthetic `{"type":"connected"}` (router.cjs:274) so a
            // 200 + connected + EOF cycle is treated as a failed reconnect,
            // not as proof of a healthy server.
            if (!receivedEvent && isSubstantiveEvent(evt)) { receivedEvent = true; reconnectAttempt = 0; }
            if (evt.type === 'message') {
              await mcp.notification({
                method: 'notifications/claude/channel',
                params: {
                  source: 'mercury-telegram',
                  label: SESSION_ID,
                  content: `<channel source="mercury-telegram" chat_id="${String(evt.from_chat).replace(/[^0-9-]/g,'')}">${xmlEsc(evt.content)}</channel>`,
                },
              });
            } else if (evt.type === 'verdict') {
              await mcp.notification({
                method: 'notifications/claude/channel/permission',
                params: { verdict: evt.verdict, request_id: evt.request_id },
              });
            } else if (evt.type === 'command') {
              // Phase C (#324): forward known payload keys (path/model/mode) as XML
              // attributes AND in body so dir/model/permission-mode commands deliver
              // the operand to the lane session, not just the bare verb.
              const chatAttr = String(evt.from_chat).replace(/[^0-9-]/g,'');
              const payloadParts = [];
              const bodyParts = [`${xmlEsc(evt.cmd)} requested by user`];
              for (const key of ['path', 'model', 'mode']) {
                if (typeof evt[key] === 'string' && evt[key].length > 0) {
                  payloadParts.push(`${key}="${xmlEsc(evt[key])}"`);
                  bodyParts.push(`${key}=${xmlEsc(evt[key])}`);
                }
              }
              const attrs = payloadParts.length ? ' ' + payloadParts.join(' ') : '';
              await mcp.notification({
                method: 'notifications/claude/channel',
                params: { source: 'mercury-telegram', label: SESSION_ID,
                  content: `<channel source="mercury-telegram" chat_id="${chatAttr}" cmd="${xmlEsc(evt.cmd)}"${attrs}>${bodyParts.join(': ')}</channel>` },
              });
            }
          } catch {}
        }
      }
      // Inner loop exited via `done: true` (normal EOF). Issue #299 / Codex
      // High: without a sleep here, the outer `while` would immediately
      // re-attach, and the router's synthetic `connected` event combined
      // with `receivedEvent`-gated reset (now only on substantive events,
      // not `connected`) would otherwise hot-loop on a 200 + connected + EOF
      // server. If this connection DID deliver a substantive event,
      // `reconnectAttempt` is already 0 so the next sleep is 1s — transient
      // flap. If it did NOT, `reconnectAttempt++` walks toward the 30s cap.
      if (sseActive) await new Promise(r => setTimeout(r, nextBackoffMs(reconnectAttempt++)));
    } catch {
      if (!sseActive) break;
      // reconnect: re-ensure router then retry. Backoff schedule per #299:
      // if this connection delivered events `reconnectAttempt` is already 0
      // (transient blip → 1s), otherwise it increments toward the 30s clamp.
      try { await ensureRouter(); } catch {}
      await new Promise(r => setTimeout(r, nextBackoffMs(reconnectAttempt++)));
    }
  }
}

// ── Graceful exit ─────────────────────────────────────────────────────────────
// Issue #297: track in-flight async re-register so shutdown can await it
// before firing deregister. Prevents the race where a late /register POST
// arrives at the router AFTER shutdown's /register DELETE, leaving a stale
// session with no live process behind it.
let pendingReregister = null;

async function shutdown() {
  sseActive = false;
  if (pendingReregister) { try { await pendingReregister; } catch {} }
  await deregister();
  process.exit(0);
}
process.on('SIGTERM', shutdown);
process.on('SIGINT',  shutdown);
// Fix H3: removed unsafe process.on('exit') execSync with string-interpolated shell command.
// SIGTERM/SIGINT + beforeExit cover all normal exit paths without shell injection risk.
process.on('beforeExit', async () => { await deregister(); });

// ── Main ──────────────────────────────────────────────────────────────────────
(async () => {
  try {
    await ensureRouter();
    // read token after router is up (router writes token file on listen success)
    _token = await readToken(10, 300);
    if (!_token) process.stderr.write(`${TAG} WARNING: could not read router token; IPC calls will be unauthenticated\n`);
    const registered = await register();
    if (registered) startInboxIfNeeded();
  } catch (e) {
    process.stderr.write(`${TAG} startup error: ${e.message}\n`);
  }
  const transport = new StdioServerTransport();
  await mcp.connect(transport);

  // Issue #297: async branch detection + initial-register retry deferred to
  // after mcp.connect() so cold start is not blocked by git. The lifecycle
  // covers two independent concerns:
  //   (a) When MERCURY_BRANCH_OVERRIDE is unset, run an async git lookup; if
  //       a real branch resolves, re-register so the router sees the correct
  //       branch for label derivation.
  //   (b) Regardless of override, if the initial register failed
  //       (inboxStarted=false), fire a retry — without this, an initial 429
  //       / transient failure leaves the session permanently unregistered
  //       and lane-targeted messages are silently dropped. (Argus iter-1 +
  //       iter-2 findings.)
  // /register at the router is an upsert and preserves sseClients.
  //
  // Shutdown-race protection (multi-layer):
  //   1. sseActive check BEFORE the re-register call — skip if shutdown
  //      already flipped the flag.
  //   2. Wrap the ENTIRE async lifecycle (detect + register) in a single
  //      `pendingReregister` Promise assigned atomically. shutdown() awaits
  //      it before firing /register DELETE, sequencing the POST before the
  //      DELETE deterministically regardless of when SIGTERM lands.
  //   3. sseActive recheck AFTER the block — defense in depth for the
  //      pathological case where SIGTERM lands after pendingReregister
  //      cleared but before we exit.
  pendingReregister = (async () => {
    try {
      let branchChanged = false;
      if (!process.env.MERCURY_BRANCH_OVERRIDE) {
        const detected = await detectBranchAsync();
        if (!sseActive) return;
        if (detected !== branch) { branch = detected; branchChanged = true; }
      }
      if (sseActive && (branchChanged || !inboxStarted)) {
        if (await register()) startInboxIfNeeded();
      }
    } catch (e) {
      process.stderr.write(`${TAG} async re-register failed: ${e.message}\n`);
    }
  })();
  try { await pendingReregister; }
  finally { pendingReregister = null; }
  if (!sseActive) await deregister();
})();
