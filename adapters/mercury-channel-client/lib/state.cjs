'use strict';

// Shared mutable state + boot-time constants for mercury-channel-client.
// Singleton (CJS modules cache once) — all submodules require this and
// read/write the same instance. Keeping state in one module is the cheapest
// fix for the original monolith's "module-level let" pattern, which doesn't
// survive being split across files without either factories or a shared
// state object. We take the shared-state object route to keep the diff a
// pure code-move with no behavior change.

const crypto = require('crypto');
const path = require('path');
const os = require('os');
const { detectBranchSync } = require('./detect-branch.cjs');

const PORT       = Number(process.env.MERCURY_ROUTER_PORT) || 8788;
const ROUTER_CJS = path.join(__dirname, '..', '..', 'mercury-channel-router', 'router.cjs');
const TOKEN_FILE = path.join(os.homedir(), '.mercury', 'router.token');
const TAG        = '[mercury-channel-client]';

// Session identity
const SESSION_ID    = process.env.CLAUDE_SESSION_ID || `cc-${process.pid}-${Date.now().toString(36)}`;
const PROJECT_PATH  = process.env.CLAUDE_PROJECT_DIR || process.cwd();
// ADR §7.6: 6-char prefix = first 6 hex chars of sha1(SESSION_ID)
const SESSION_SHORT = crypto.createHash('sha1').update(SESSION_ID).digest('hex').slice(0, 6);

module.exports = {
  // Constants
  PORT, ROUTER_CJS, TOKEN_FILE, TAG, SESSION_ID, SESSION_SHORT, PROJECT_PATH,
  // Mutable state
  // Issue #297: branch detection deferred to async path so module load is not
  // blocked by `git branch --show-current` (was 50-200ms typical, longer if
  // git hung). Initial value comes from MERCURY_BRANCH_OVERRIDE env var or
  // 'unknown'; async git lookup runs after mcp.connect() and re-registers.
  branch: detectBranchSync(),
  _token: null,
  sseActive: true,
  // Issue #297 R3-M1: track whether connectInbox() has been wired. The initial
  // register() path may fail (429 cap, transient) but the deferred async
  // re-register may later succeed; without this flag the late-success path
  // would leave the session registered at the router with no SSE consumer,
  // silently dropping lane-targeted messages.
  inboxStarted: false,
  // Issue #297: track in-flight async re-register so shutdown can await it
  // before firing deregister. Prevents the race where a late /register POST
  // arrives at the router AFTER shutdown's /register DELETE, leaving a stale
  // session with no live process behind it.
  pendingReregister: null,
};
