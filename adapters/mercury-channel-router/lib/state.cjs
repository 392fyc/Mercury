'use strict';

// Shared mutable state + boot-time constants for mercury-channel-router.
// Singleton (CJS modules cache once) — all submodules require this and
// read/write the same instance. Keeping state in one module is the cheapest
// fix for the original monolith's "module-level let" pattern, which doesn't
// survive being split across files without either factories or a shared
// state object. We take the shared-state object route to keep the diff a
// pure code-move with no behavior change.

const path = require('path');
const os = require('os');
const crypto = require('crypto');

const PORT       = Number(process.env.MERCURY_ROUTER_PORT) || 8788;
const LOCK_FILE  = path.join(os.homedir(), '.mercury', 'router.lock');
const TOKEN_FILE = path.join(os.homedir(), '.mercury', 'router.token');
// Phase C (#324): bump default 3→5 to match feedback_lane_protocol.md HARD-CAP.
// MERCURY_ROUTER_MAX_SESS env override is floored to a positive integer; non-finite
// or non-positive values fall back to default 5 so the cap is always integer.
const MAX_SESS_RAW = Number(process.env.MERCURY_ROUTER_MAX_SESS);
const MAX_SESS = Number.isFinite(MAX_SESS_RAW) && MAX_SESS_RAW >= 1 ? Math.floor(MAX_SESS_RAW) : 5;
const TAG = '[mercury-channel-router]';

// IPC auth token — written to TOKEN_FILE after server.listen succeeds.
const TOKEN = crypto.randomBytes(16).toString('hex');

module.exports = {
  // Constants (read-only references; assignments would no-op on require cache hits)
  PORT, LOCK_FILE, TOKEN_FILE, MAX_SESS, TAG, TOKEN,
  // Mutable state — set by submodules during startup / runtime
  sessions: new Map(),
  activeId: null,
  shutdownTimer: null,
  startTs: Date.now(),
  lastChatId: null,
  bot: null,
};
