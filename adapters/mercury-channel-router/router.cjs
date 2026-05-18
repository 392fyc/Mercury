#!/usr/bin/env node
'use strict';

// Mercury Channel Router — long-running Telegram bot + IPC server.
// One instance per machine; spawned by mercury-channel-client on first session.
//
// Issue #303 split: router.cjs is now wiring only. Behavior lives in:
//   lib/state.cjs    — shared mutable state object + boot-time constants
//   lib/telegram.cjs — bot init, tgSend, htmlEsc, isAllowed, TG_SEND_* constants
//   lib/ipc.cjs      — http server factory + token + lock file management
//   lib/routing.cjs  — message dispatch, command handlers, label derivation
// Pre-existing helper libs (env.cjs, truncate.cjs) are unchanged.

const state    = require('./lib/state.cjs');
const telegram = require('./lib/telegram.cjs');
const ipc      = require('./lib/ipc.cjs');
const routing  = require('./lib/routing.cjs');
const callback = require('./lib/callback.cjs');

const { PORT, TAG } = state;

const server = ipc.createServer({
  deriveLabel:      routing.deriveLabel,
  scheduleShutdown: routing.scheduleShutdown,
  tgSend:           telegram.tgSend,
  htmlEsc:          telegram.htmlEsc,
});

// Startup: listen first, then acquire lock + write token + start Telegram polling.
// Issue #304 nit 4: bot init is sequenced AFTER acquireLock() so a second
// router process never starts polling Telegram (and never wins/loses a 409
// race against the running instance) — its server.listen EADDRINUSE handler
// fires first and exits before initBot() runs. AND on this (lock-owning)
// process we gate writeToken()+initBot() on acquireLock() returning true,
// so a filesystem-level lock failure aborts startup rather than silently
// polling Telegram without singleton ownership (Codex sync-audit R2 M1).
//
// Issue #303: routeMessage is passed into initBot() so the listener wiring
// stays atomic with bot construction (previously a top-level
// `bot.on('message', routeMessage)` had to run after initBot). Passing the
// callback also avoids a telegram.cjs → routing.cjs import cycle (routing
// already imports tgSend from telegram).
server.listen(PORT, '127.0.0.1', () => {
  process.stderr.write(`${TAG} IPC server listening on 127.0.0.1:${PORT}\n`);
  if (!ipc.acquireLock()) {
    process.stderr.write(`${TAG} aborting startup; Telegram polling not started\n`);
    process.exit(1);
  }
  ipc.writeToken();
  // Issue #307: pass routeCallback as the 2nd handler so grammy wires
  // bot.on('callback_query:data', ...) alongside the message handler.
  telegram.initBot(routing.routeMessage, callback.routeCallback);
});
server.on('error', e => { process.stderr.write(`${TAG} server error: ${e.message}\n`); process.exit(1); });
// do NOT releaseLock on server error — lock may not have been acquired yet

const cleanup = () => { ipc.releaseLock(); ipc.cleanupToken(); };
process.on('SIGTERM', () => { cleanup(); process.exit(0); });
process.on('SIGINT',  () => { cleanup(); process.exit(0); });
