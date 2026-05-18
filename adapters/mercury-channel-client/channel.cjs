#!/usr/bin/env node
'use strict';

// Mercury Channel Client — MCP server bridging Claude Code sessions to the
// channel router. Loaded by Claude Code via .mcp.json; one instance per
// session.
//
// Issue #303 split: channel.cjs is now wiring only. Behavior lives in:
//   lib/state.cjs           — shared mutable state object + boot-time constants
//   lib/router-bootstrap.cjs — readToken / getToken / routerFetch / ensureRouter / (de)register
//   lib/mcp-tools.cjs       — MCP server + reply tool + permission relay
//   lib/sse.cjs             — connectInbox + startInboxIfNeeded
// Pre-existing helper libs (backoff.cjs, detect-branch.cjs, token-refresh.cjs)
// are unchanged.

const { StdioServerTransport } = require('@modelcontextprotocol/sdk/server/stdio.js');
const state = require('./lib/state.cjs');
const bootstrap = require('./lib/router-bootstrap.cjs');
const { mcp } = require('./lib/mcp-tools.cjs');
const { startInboxIfNeeded } = require('./lib/sse.cjs');
const { detectBranchAsync } = require('./lib/detect-branch.cjs');

const { TAG } = state;

// Issue #304: initial token read budget (router-startup race).
const READ_TOKEN_BOOT_RETRIES  = 10;
const READ_TOKEN_BOOT_DELAY_MS = 300;

// Graceful exit
async function shutdown() {
  state.sseActive = false;
  if (state.pendingReregister) { try { await state.pendingReregister; } catch {} }
  await bootstrap.deregister();
  process.exit(0);
}
process.on('SIGTERM', shutdown);
process.on('SIGINT',  shutdown);
// Fix H3: removed unsafe process.on('exit') execSync with string-interpolated shell command.
// SIGTERM/SIGINT + beforeExit cover all normal exit paths without shell injection risk.
process.on('beforeExit', async () => { await bootstrap.deregister(); });

// Main
(async () => {
  try {
    await bootstrap.ensureRouter();
    // read token after router is up (router writes token file on listen success)
    state._token = await bootstrap.readToken(READ_TOKEN_BOOT_RETRIES, READ_TOKEN_BOOT_DELAY_MS);
    if (!state._token) process.stderr.write(`${TAG} WARNING: could not read router token; IPC calls will be unauthenticated\n`);
    const registered = await bootstrap.register();
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
  //       and lane-targeted messages are silently dropped.
  // /register at the router is an upsert and preserves sseClients.
  //
  // Shutdown-race protection (multi-layer):
  //   1. sseActive check BEFORE the re-register call.
  //   2. Wrap the ENTIRE async lifecycle (detect + register) in a single
  //      `pendingReregister` Promise. shutdown() awaits it before firing
  //      /register DELETE, sequencing POST before DELETE deterministically.
  //   3. sseActive recheck AFTER the block — defense in depth.
  state.pendingReregister = (async () => {
    try {
      let branchChanged = false;
      if (!process.env.MERCURY_BRANCH_OVERRIDE) {
        const detected = await detectBranchAsync();
        if (!state.sseActive) return;
        if (detected !== state.branch) { state.branch = detected; branchChanged = true; }
      }
      if (state.sseActive && (branchChanged || !state.inboxStarted)) {
        if (await bootstrap.register()) startInboxIfNeeded();
      }
    } catch (e) {
      process.stderr.write(`${TAG} async re-register failed: ${e.message}\n`);
    }
  })();
  try { await state.pendingReregister; }
  finally { state.pendingReregister = null; }
  if (!state.sseActive) await bootstrap.deregister();
})();
