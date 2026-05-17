'use strict';

// Issue #304 regression — structural invariants for the notify-hub housekeeping
// PR. These nits are about source-level structure (named constants, deferred
// initialization, parameter naming) rather than runtime behavior, so the
// cheapest robust test is a source-text contract:
//   - constants present near the top with the documented values
//   - `initBot()` exists and is invoked from inside server.listen callback
//   - the legacy top-level `bot.on('message', routeMessage)` wiring is gone
// Behavioral verification of `initBot` deferral would require mocking
// `node-telegram-bot-api` + the http listener and produce a brittle, slow
// integration test for a small no-op-on-disabled function. The structural
// assertions catch the regression we actually care about: that a future
// edit doesn't accidentally re-introduce the pre-#304 race window.

const test   = require('node:test');
const assert = require('node:assert/strict');
const fs     = require('node:fs');
const path   = require('node:path');

const routerSrc = fs.readFileSync(path.join(__dirname, '..', 'router.cjs'), 'utf8');

test('#304 nit 1: router.cjs magic numbers extracted to named constants', () => {
  assert.match(routerSrc, /const\s+SHUTDOWN_GRACE_MS\s*=\s*30000/);
  assert.match(routerSrc, /const\s+LOCK_ACQUIRE_RETRIES\s*=\s*3/);
  assert.match(routerSrc, /const\s+TG_SEND_MAX_RETRIES\s*=\s*2/);
  assert.match(routerSrc, /const\s+TG_SEND_RETRY_AFTER_MAX_S\s*=\s*5/);
  // Call sites must reference the named constant — every former literal
  // must be gone. Cover all four extracted constants symmetrically.
  assert.doesNotMatch(routerSrc, /setTimeout\([^,]+,\s*30000\)/);   // SHUTDOWN_GRACE_MS
  assert.doesNotMatch(routerSrc, /for\s*\(\s*let\s+i\s*=\s*0\s*;\s*i\s*<\s*3\s*;/); // LOCK_ACQUIRE_RETRIES
  assert.doesNotMatch(routerSrc, /attempt\s*<\s*2\s*;/);            // TG_SEND_MAX_RETRIES
  assert.doesNotMatch(routerSrc, /ra\s*<=\s*5\b/);                  // TG_SEND_RETRY_AFTER_MAX_S
});

test('#304 nit 4: bot init deferred — initBot() exists and runs after acquireLock()', () => {
  // initBot must be a top-level function declaration.
  assert.match(routerSrc, /function\s+initBot\s*\(\s*\)\s*\{/);
  // initBot must be invoked from the server.listen startup block, AFTER
  // acquireLock() + writeToken(). The callback is the third arg to
  // server.listen, so we anchor on the startup sequence rather than trying
  // to bracket-match the arg list. We assert the three calls appear in
  // strict order — acquireLock first, then writeToken, then initBot —
  // without requiring an exact punctuation shape (the M1 fix wraps
  // acquireLock in `if (!acquireLock()) { ...exit... }` rather than a
  // bare semi-terminated call).
  assert.match(
    routerSrc,
    /server\.listen\([\s\S]*?acquireLock\(\)[\s\S]*?writeToken\(\)[\s\S]*?initBot\(\)/,
  );
  // Codex sync-audit R2 M1: acquireLock() return value MUST gate startup.
  // A bare `acquireLock(); writeToken(); initBot();` would re-open the
  // case where the lock acquisition silently no-ops and Telegram polling
  // proceeds anyway.
  assert.match(routerSrc, /if\s*\(\s*!\s*acquireLock\(\)\s*\)/);
  // The pre-#304 top-level `if (bot) bot.on('message', routeMessage);` line
  // must not survive — the wiring now lives inside initBot().
  assert.doesNotMatch(routerSrc, /^if \(bot\) bot\.on\('message',\s*routeMessage\)/m);
  // Codex sync-audit Low finding: assert that `new TelegramBot(...)` ONLY
  // appears inside initBot(). A regression that re-introduces a top-level
  // TelegramBot constructor — while keeping initBot intact — would reopen
  // the 409 race the rest of this PR closes. We allow exactly one
  // `new TelegramBot` *executable* occurrence, and require it to appear
  // AFTER the `function initBot` declaration in source order.
  //
  // Strip single-line `//` comments before counting — the file legitimately
  // mentions `new TelegramBot({polling:true})` in a #304 nit-4 explanatory
  // comment, and that mention must not be counted against the constructor
  // budget. (Block `/* */` comments are not used in this file.)
  // `[^\n]*` instead of `.*` — `.` excludes `\r`, so on Windows CRLF lines
  // the strip would otherwise miss everything after `//` on each line.
  const stripped   = routerSrc.split('\n').map(l => l.replace(/\/\/[^\n]*$/, '')).join('\n');
  const tgMatches  = [...stripped.matchAll(/new\s+TelegramBot\b/g)];
  assert.equal(tgMatches.length, 1, 'expected exactly one executable `new TelegramBot` constructor call (inside initBot)');
  const initBotIdx = stripped.indexOf('function initBot');
  assert.ok(initBotIdx >= 0, 'function initBot declaration must exist');
  assert.ok(tgMatches[0].index > initBotIdx,
    '`new TelegramBot` must appear inside or after `function initBot`, never at module scope above it');
});
