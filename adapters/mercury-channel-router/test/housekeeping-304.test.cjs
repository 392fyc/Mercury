'use strict';

// Issue #304 regression — structural invariants for the notify-hub housekeeping
// PR. These nits are about source-level structure (named constants, deferred
// initialization, parameter naming) rather than runtime behavior, so the
// cheapest robust test is a source-text contract:
//   - constants present in their owning submodules with the documented values
//   - `initBot()` exists and is invoked from inside server.listen callback
//   - the legacy top-level `bot.on('message', routeMessage)` wiring is gone
// Behavioral verification of `initBot` deferral would require mocking
// `grammy` + the http listener and produce a brittle, slow
// integration test for a small no-op-on-disabled function. The structural
// assertions catch the regression we actually care about: that a future
// edit doesn't accidentally re-introduce the pre-#304 race window.
//
// #303 split: router.cjs is now wiring only; constants and helpers moved to
// lib/{state,telegram,ipc,routing}.cjs. Each constant is asserted in its
// owning submodule so a future re-extraction does not silently rehome the
// named constants under a different name.

const test   = require('node:test');
const assert = require('node:assert/strict');
const fs     = require('node:fs');
const path   = require('node:path');

const read = rel => fs.readFileSync(path.join(__dirname, '..', rel), 'utf8');

const routerSrc   = read('router.cjs');
const stateSrc    = read('lib/state.cjs');
const telegramSrc = read('lib/telegram.cjs');
const ipcSrc      = read('lib/ipc.cjs');
const routingSrc  = read('lib/routing.cjs');
// Issue #407 F1: body.cjs split off from ipc.cjs to keep both under the
// #303 ≤200 LOC contract — same structural cap applies.
const bodySrc     = read('lib/body.cjs');

test('#304 nit 1: magic numbers extracted to named constants in owning submodules', () => {
  // SHUTDOWN_GRACE_MS lives with scheduleShutdown() in routing.cjs.
  assert.match(routingSrc,  /const\s+SHUTDOWN_GRACE_MS\s*=\s*30000/);
  // LOCK_ACQUIRE_RETRIES lives with acquireLock() in ipc.cjs.
  assert.match(ipcSrc,      /const\s+LOCK_ACQUIRE_RETRIES\s*=\s*3/);
  // TG_SEND_* lives with tgSend() in telegram.cjs.
  assert.match(telegramSrc, /const\s+TG_SEND_MAX_RETRIES\s*=\s*2/);
  assert.match(telegramSrc, /const\s+TG_SEND_RETRY_AFTER_MAX_S\s*=\s*5/);
  // Call sites must reference the named constant — every former literal
  // must be gone. Cover all four extracted constants symmetrically across
  // the whole package.
  const allSrc = routerSrc + telegramSrc + ipcSrc + routingSrc;
  assert.doesNotMatch(allSrc, /setTimeout\([^,]+,\s*30000\)/);   // SHUTDOWN_GRACE_MS
  assert.doesNotMatch(allSrc, /for\s*\(\s*let\s+i\s*=\s*0\s*;\s*i\s*<\s*3\s*;/); // LOCK_ACQUIRE_RETRIES
  assert.doesNotMatch(allSrc, /attempt\s*<\s*2\s*;/);            // TG_SEND_MAX_RETRIES
  assert.doesNotMatch(allSrc, /ra\s*<=\s*5\b/);                  // TG_SEND_RETRY_AFTER_MAX_S
});

test('#304 nit 4: bot init deferred — initBot() exists and runs after acquireLock()', () => {
  // initBot must be a top-level function declaration in telegram.cjs.
  assert.match(telegramSrc, /function\s+initBot\s*\(/);
  // initBot must be invoked from the server.listen startup block in
  // router.cjs (the wiring entry), AFTER acquireLock() + writeToken(). The
  // callback is the third arg to server.listen, so we anchor on the startup
  // sequence rather than trying to bracket-match the arg list. We assert
  // the three calls appear in strict order — acquireLock first, then
  // writeToken, then initBot — without requiring an exact punctuation shape
  // (the M1 fix wraps acquireLock in `if (!acquireLock()) { ...exit... }`
  // rather than a bare semi-terminated call).
  assert.match(
    routerSrc,
    /server\.listen\([\s\S]*?acquireLock\(\)[\s\S]*?writeToken\(\)[\s\S]*?initBot\(/,
  );
  // Codex sync-audit R2 M1: acquireLock() return value MUST gate startup.
  // A bare `acquireLock(); writeToken(); initBot();` would re-open the
  // case where the lock acquisition silently no-ops and Telegram polling
  // proceeds anyway.
  assert.match(routerSrc, /if\s*\(\s*!\s*(?:ipc\.)?acquireLock\(\)\s*\)/);
  // The pre-#304 top-level `if (bot) bot.on('message', routeMessage);` line
  // must not survive anywhere in the package — the wiring now lives inside
  // initBot()'s `state.bot.on('message', onMessage)` callback path.
  const allSrc = routerSrc + telegramSrc + ipcSrc + routingSrc;
  assert.doesNotMatch(allSrc, /^if \(bot\) bot\.on\('message',\s*routeMessage\)/m);
  // Codex sync-audit Low finding: assert that `new Bot(...)` ONLY appears
  // inside initBot(). A regression that re-introduces a top-level Bot
  // constructor — while keeping initBot intact — would reopen the 409 race
  // the rest of this PR closes. After the #303 split, telegram.cjs is the
  // only file that should reference grammy's Bot constructor at all.
  // Issue #302 + Copilot iter-1 C4: regex matches `new Bot` (grammy). The
  // `\s+` REQUIRES whitespace immediately before `Bot`, which is what excludes
  // `new TelegramBot` (no whitespace between `Telegram` and `Bot`). The
  // trailing `\b` then ensures `Bot` is a whole word (so a hypothetical
  // `BotImpl` would not match either). An accidental rollback to
  // node-telegram-bot-api would fail this assertion via the `\s+` exclusion.
  // Strip single-line `//` comments before counting — the explanatory
  // comments in telegram.cjs legitimately mention Bot/TelegramBot, and
  // those mentions must not be counted against the constructor budget.
  // `[^\n]*` instead of `.*` — `.` excludes `\r`, so on Windows CRLF lines
  // the strip would otherwise miss everything after `//` on each line.
  const stripped   = telegramSrc.split('\n').map(l => l.replace(/\/\/[^\n]*$/, '')).join('\n');
  const tgMatches  = [...stripped.matchAll(/new\s+Bot\b/g)];
  assert.equal(tgMatches.length, 1, 'expected exactly one executable `new Bot` constructor call (inside initBot)');
  const initBotIdx = stripped.indexOf('function initBot');
  assert.ok(initBotIdx >= 0, 'function initBot declaration must exist in telegram.cjs');
  assert.ok(tgMatches[0].index > initBotIdx,
    '`new Bot` must appear inside or after `function initBot`, never at module scope above it');
  // No other submodule should construct a Bot — that would defeat the
  // lock-gated singleton contract.
  for (const [name, src] of [['router.cjs', routerSrc], ['lib/ipc.cjs', ipcSrc], ['lib/routing.cjs', routingSrc], ['lib/body.cjs', bodySrc]]) {
    const stripped2 = src.split('\n').map(l => l.replace(/\/\/[^\n]*$/, '')).join('\n');
    assert.equal((stripped2.match(/new\s+Bot\b/g) || []).length, 0,
      `${name} must not construct a Bot`);
  }
});

test('#303 split: router.cjs is wiring only (≤200 LOC) and all submodules ≤200 LOC', () => {
  // Issue #303 acceptance: each submodule ≤200 LOC after the architectural
  // split. A regression that re-monolithizes router.cjs would push it back
  // over the cap and reintroduce the legibility / review-iteration problems
  // PR #295 surfaced.
  // Strip a single trailing newline (common editor behavior) before counting,
  // and split on CRLF or LF — Copilot finding on PR #406. Without the strip,
  // `"a\nb\n".split('\n').length === 3` would over-count a 2-line file as 3.
  const lineCount = src => src.replace(/\r?\n$/, '').split(/\r?\n/).length;
  const limits = {
    'router.cjs':       lineCount(routerSrc),
    'lib/state.cjs':    lineCount(stateSrc),
    'lib/telegram.cjs': lineCount(telegramSrc),
    'lib/ipc.cjs':      lineCount(ipcSrc),
    'lib/routing.cjs':  lineCount(routingSrc),
    'lib/body.cjs':     lineCount(bodySrc),
  };
  for (const [name, lines] of Object.entries(limits)) {
    assert.ok(lines <= 200, `${name} is ${lines} LOC; #303 acceptance caps each submodule at ≤200`);
  }
});
