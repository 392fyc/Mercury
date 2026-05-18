'use strict';

// callback-307.test.cjs — Issue #307: Telegram inline_keyboard buttons for
// permission approval (CCGram UX). Three test layers:
//
//   1. Pure-function unit tests for parseVerdictCallback + buildVerdictKeyboard
//      (no router boot, no grammy) — fast feedback on the callback_data shape
//      and the keyboard JSON.
//   2. Structural assertions that the dispatch path is wired end-to-end:
//      ipc.cjs imports buildVerdictKeyboard, telegram.cjs's initBot accepts
//      onCallback and registers `bot.on('callback_query:data', ...)`,
//      router.cjs passes callback.routeCallback as the 2nd initBot arg, and
//      routeCallback gates on isAllowed before sendToInbox.
//   3. Behavioral test that the live /permission-request HTTP endpoint still
//      returns 200 after the inline_keyboard wiring (regression for the route
//      itself; the dispatch-via-Telegram path can't be exercised without a
//      live bot, which the existing tests intentionally avoid — see
//      housekeeping-304.test.cjs:11-14 rationale).

const { test } = require('node:test');
const assert  = require('node:assert/strict');
const { spawn } = require('child_process');
const path = require('path');
const fs   = require('fs');
const os   = require('os');

const ROOT = path.resolve(__dirname, '..');
const ROUTER = path.join(ROOT, 'router.cjs');
const read = rel => fs.readFileSync(path.join(ROOT, rel), 'utf8');

const callback   = require('../lib/callback.cjs');
const callbackSrc = read('lib/callback.cjs');
const telegramSrc = read('lib/telegram.cjs');
const ipcSrc      = read('lib/ipc.cjs');
const routerSrc   = read('router.cjs');

// ─── Layer 1: parseVerdictCallback ───────────────────────────────────────────

test('parseVerdictCallback accepts the documented y/n verdicts with valid prefixed_id', () => {
  assert.deepEqual(
    callback.parseVerdictCallback('v:y:abc123-defgh'),
    { verdict: 'yes', shortId: 'abc123', requestId: 'defgh' },
  );
  assert.deepEqual(
    callback.parseVerdictCallback('v:n:abc123-defgh'),
    { verdict: 'no',  shortId: 'abc123', requestId: 'defgh' },
  );
});

test('parseVerdictCallback rejects malformed shapes (anchored regex)', () => {
  // Non-string
  assert.equal(callback.parseVerdictCallback(undefined), null);
  assert.equal(callback.parseVerdictCallback(null), null);
  assert.equal(callback.parseVerdictCallback(42), null);
  // Wrong prefix
  assert.equal(callback.parseVerdictCallback('verdict:y:abc123-defgh'), null);
  assert.equal(callback.parseVerdictCallback('p:y:abc123-defgh'), null);
  // Wrong verdict char
  assert.equal(callback.parseVerdictCallback('v:z:abc123-defgh'), null);
  assert.equal(callback.parseVerdictCallback('v:yes:abc123-defgh'), null);
  // Wrong shortId charset
  assert.equal(callback.parseVerdictCallback('v:y:ABC123-defgh'), null);
  assert.equal(callback.parseVerdictCallback('v:y:abc-12-defgh'), null);
  // Wrong requestId length — must be exactly 5 chars after the dash.
  assert.equal(callback.parseVerdictCallback('v:y:abc123-deflgh'), null); // 6 chars (too long)
  assert.equal(callback.parseVerdictCallback('v:y:abc123-defg'),   null); // 4 chars (too short)
  // Wrong requestId charset — `l` excluded per routeMessage's regex.
  assert.equal(callback.parseVerdictCallback('v:y:abc123-deflg'),  null); // 5 chars, contains 'l'
  // Padding / smuggled suffix
  assert.equal(callback.parseVerdictCallback('v:y:abc123-defgh extra'), null);
  assert.equal(callback.parseVerdictCallback(' v:y:abc123-defgh'), null);
  assert.equal(callback.parseVerdictCallback(''), null);
});

test('VERDICT_CB_RE accepts the canonical shape exactly', () => {
  assert.match('v:y:000000-abcde', callback.VERDICT_CB_RE);
  assert.match('v:n:zzzzzz-mnopq', callback.VERDICT_CB_RE);
  assert.doesNotMatch('v:y:abc123-def',   callback.VERDICT_CB_RE);
  assert.doesNotMatch('v:y:abc123-defghi', callback.VERDICT_CB_RE);
});

// ─── Layer 1: buildVerdictKeyboard ───────────────────────────────────────────

test('buildVerdictKeyboard returns a two-button inline_keyboard with v:y / v:n callback_data', () => {
  const kb = callback.buildVerdictKeyboard('abc123-defgh');
  assert.deepEqual(kb, {
    inline_keyboard: [[
      { text: '✅ Allow', callback_data: 'v:y:abc123-defgh' },
      { text: '❌ Deny',  callback_data: 'v:n:abc123-defgh' },
    ]],
  });
});

test('buildVerdictKeyboard callback_data stays within Telegram\'s 1-64 byte limit (inclusive)', () => {
  // Real prefixed_request_id shape (12 chars). The format prefix `v:y:` is 4
  // bytes — total 16 bytes UTF-8, well under 64. Telegram Bot API specifies
  // callback_data as "1-64 bytes" inclusive (Copilot iter-2 finding: a strict
  // `< 64` assertion would falsely reject a valid 64-byte payload). Asserts
  // both upper bound (≤64) and lower bound (≥1) since both are documented.
  const kb = callback.buildVerdictKeyboard('abc123-defgh');
  for (const btn of kb.inline_keyboard[0]) {
    const bytes = Buffer.byteLength(btn.callback_data, 'utf8');
    assert.ok(bytes >= 1 && bytes <= 64, `callback_data ${JSON.stringify(btn.callback_data)} is ${bytes} bytes; Telegram requires 1-64 bytes inclusive`);
  }
});

// ─── Layer 2: structural wiring ──────────────────────────────────────────────

test('callback.cjs exports the public symbols routeCallback + ipc.cjs need', () => {
  assert.equal(typeof callback.VERDICT_CB_RE,            'object');           // RegExp
  assert.equal(typeof callback.PREFIXED_REQUEST_ID_RE,   'object');           // RegExp (Codex Medium fix)
  assert.equal(typeof callback.parseVerdictCallback,     'function');
  assert.equal(typeof callback.isValidPrefixedRequestId, 'function');
  assert.equal(typeof callback.buildVerdictKeyboard,     'function');
  assert.equal(typeof callback.buildPermissionRequestText, 'function');
  assert.equal(typeof callback.routeCallback,            'function');
});

// ─── Codex iter-1 Medium: isValidPrefixedRequestId boundary check ────────────

test('isValidPrefixedRequestId accepts canonical <6>-<5> shape only (Codex Medium)', () => {
  // Canonical shape from routing.cjs::routeMessage verdict regex.
  assert.equal(callback.isValidPrefixedRequestId('abc123-defgh'), true);
  assert.equal(callback.isValidPrefixedRequestId('000000-abcde'), true);
  assert.equal(callback.isValidPrefixedRequestId('zzzzzz-mnopq'), true);
  // Reject malformed shapes that would silently break the keyboard.
  assert.equal(callback.isValidPrefixedRequestId(''),                false);
  assert.equal(callback.isValidPrefixedRequestId(undefined),         false);
  assert.equal(callback.isValidPrefixedRequestId(null),              false);
  assert.equal(callback.isValidPrefixedRequestId(42),                false);
  assert.equal(callback.isValidPrefixedRequestId('abc123defgh'),     false); // missing dash
  assert.equal(callback.isValidPrefixedRequestId('ABC123-defgh'),    false); // uppercase shortId
  assert.equal(callback.isValidPrefixedRequestId('abc123-deflg'),    false); // 'l' excluded
  assert.equal(callback.isValidPrefixedRequestId('abc123-defghi'),   false); // too long
  assert.equal(callback.isValidPrefixedRequestId('abc12-defgh'),     false); // shortId too short
  // Padding / smuggle attempts — anchored regex must reject.
  assert.equal(callback.isValidPrefixedRequestId(' abc123-defgh'),   false);
  assert.equal(callback.isValidPrefixedRequestId('abc123-defgh '),   false);
  assert.equal(callback.isValidPrefixedRequestId('abc123-defgh\n'),  false);
  // 64-byte-overflow attempt — a 200-char id would push callback_data past
  // Telegram's hard limit; the boundary check refuses it cheaply.
  assert.equal(callback.isValidPrefixedRequestId('a'.repeat(200)),   false);
});

test('buildPermissionRequestText assembles the documented message body', () => {
  // Identity htmlEsc to keep the assertion focused on shape; the real
  // telegram.cjs htmlEsc is exercised end-to-end by the spawn-router test.
  const id = x => String(x);
  const txt = callback.buildPermissionRequestText('Bash', 'rm -rf /', 'abc123-defgh', id);
  assert.match(txt, /Claude wants to run Bash: rm -rf \//);
  assert.match(txt, /Tap a button below, or reply <code>yes abc123-defgh<\/code> \/ <code>no abc123-defgh<\/code>/);
});

test('routeCallback enforces private-chat-only (Codex Low: mirrors routeMessage line 158)', () => {
  // Structural: callback.cjs must reject non-private chats BEFORE the
  // allowlist gate so the previously-private-only inbound surface stays
  // private-only. Source order check — private check must precede isAllowed
  // and sendToInbox, just like routing.cjs::routeMessage does for text.
  const privateIdx   = callbackSrc.indexOf("chat?.type !== 'private'");
  const isAllowedIdx = callbackSrc.indexOf('isAllowed(');
  const sendInboxIdx = callbackSrc.indexOf('sendToInbox(');
  assert.ok(privateIdx > 0,                   'routeCallback must reject non-private chats');
  assert.ok(privateIdx < isAllowedIdx,        'private-chat check must precede allowlist gate');
  assert.ok(privateIdx < sendInboxIdx,        'private-chat check must precede inbox dispatch');
});

test('routeCallback gates on isAllowed BEFORE sendToInbox dispatch', () => {
  // Defense-in-depth structural check: a future refactor that moved the
  // dispatch above the allowlist check would let an unauthorized Telegram
  // user inject verdicts. The source order must keep isAllowed first.
  const isAllowedIdx = callbackSrc.indexOf('isAllowed(');
  const sendInboxIdx = callbackSrc.indexOf('sendToInbox(');
  assert.ok(isAllowedIdx > 0, 'routeCallback must call isAllowed');
  assert.ok(sendInboxIdx > 0, 'routeCallback must call sendToInbox');
  assert.ok(isAllowedIdx < sendInboxIdx,
    'allowlist check must precede inbox dispatch in source order');
});

test('routeCallback always calls answerCallbackQuery (Telegram requires ack)', () => {
  // Telegram clients spin the button until the bot calls answerCallbackQuery.
  // The handler must ack on every code path — allow, deny, unauthorized,
  // unparseable, session-not-found. Source-text check: the helper alias
  // `ack(...)` should appear ≥4 times (one per branch).
  const ackCalls = [...callbackSrc.matchAll(/\back\(/g)];
  assert.ok(ackCalls.length >= 4,
    `expected ≥4 ack() invocations in routeCallback; found ${ackCalls.length}`);
});

test('ipc.cjs /permission-request attaches reply_markup via buildVerdictKeyboard + validates input', () => {
  assert.match(ipcSrc, /require\('\.\/callback\.cjs'\)/);
  assert.match(ipcSrc, /buildVerdictKeyboard\(prefixed_request_id\)/);
  // The reply_markup key must be passed through tgSend's opts arg — verifies
  // the wiring from /permission-request through to the Telegram API.
  assert.match(ipcSrc, /reply_markup:\s*buildVerdictKeyboard/);
  // Codex iter-1 Medium: validate prefixed_request_id at the IPC boundary so
  // a malformed upstream value does not silently produce an unparsable
  // button. The 400 short-circuit must come BEFORE the tgSend call.
  assert.match(ipcSrc, /isValidPrefixedRequestId\(prefixed_request_id\)/);
  const validateIdx = ipcSrc.indexOf('isValidPrefixedRequestId(prefixed_request_id)');
  const tgSendIdx   = ipcSrc.indexOf('buildVerdictKeyboard(prefixed_request_id)');
  assert.ok(validateIdx > 0 && validateIdx < tgSendIdx,
    'isValidPrefixedRequestId check must run before the Telegram send');
  // The message body must flow through buildPermissionRequestText so the
  // 226-char one-liner is no longer in ipc.cjs (Claude review LOW #3).
  assert.match(ipcSrc, /buildPermissionRequestText\(/);
});

test('telegram.cjs initBot accepts onCallback and registers callback_query:data handler', () => {
  // initBot's second parameter must be onCallback (named for grep-ability).
  assert.match(telegramSrc, /function\s+initBot\s*\(\s*onMessage\s*,\s*onCallback\s*\)/);
  // The grammy registration must filter on `:data` so non-data callbacks
  // (game/inline-mode) are ignored at the bot level.
  assert.match(telegramSrc, /state\.bot\.on\(\s*'callback_query:data'/);
  // tgSend signature must accept opts so reply_markup can pass through.
  assert.match(telegramSrc, /async function tgSend\(\s*chatId\s*,\s*text\s*,\s*opts\s*\)/);
  // The reply_markup field must be forwarded to grammy's sendMessage.
  assert.match(telegramSrc, /sendOpts\.reply_markup\s*=\s*opts\.reply_markup/);
});

test('router.cjs passes callback.routeCallback as the 2nd initBot argument', () => {
  // Anchor on the initBot call in the server.listen callback. Strip line
  // comments first so a hypothetical example in a `//` comment cannot
  // satisfy the assertion.
  const stripped = routerSrc.split('\n').map(l => l.replace(/\/\/[^\n]*$/, '')).join('\n');
  assert.match(stripped, /telegram\.initBot\(\s*routing\.routeMessage\s*,\s*callback\.routeCallback\s*\)/);
  // And the top-level require must be present.
  assert.match(stripped, /require\('\.\/lib\/callback\.cjs'\)/);
});

// ─── Layer 3: behavioral — /permission-request still returns 200 ─────────────

function pickPort() { return 17000 + Math.floor(Math.random() * 1000); }

async function withRouter(extraEnv, body) {
  const PORT = pickPort();
  const tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'mcr-307-'));
  const tokenFile = path.join(tmpHome, '.mercury', 'router.token');
  const env = {
    ...process.env,
    HOME: tmpHome,
    USERPROFILE: tmpHome,
    MERCURY_ROUTER_PORT: String(PORT),
    MERCURY_NOTIFY_DISABLED: '1',
    MERCURY_TELEGRAM_BOT_TOKEN: '',
    MERCURY_TELEGRAM_ALLOWED_USER_IDS: '',
    ...extraEnv,
  };
  const child = spawn(process.execPath, [ROUTER], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  let listening = false;
  child.stderr.on('data', d => { if (String(d).includes(`listening on 127.0.0.1:${PORT}`)) listening = true; });
  for (let i = 0; i < 50 && !listening; i++) await new Promise(r => setTimeout(r, 100));
  if (!listening) { child.kill('SIGKILL'); throw new Error(`router did not start on ${PORT}`); }
  for (let i = 0; i < 20 && !fs.existsSync(tokenFile); i++) await new Promise(r => setTimeout(r, 50));
  const token = fs.readFileSync(tokenFile, 'utf8').trim();
  try {
    await body({ PORT, token });
  } finally {
    child.kill('SIGTERM');
    const exited = await new Promise(resolve => {
      if (child.exitCode !== null || child.signalCode !== null) { resolve(true); return; }
      const t = setTimeout(() => { child.removeListener('exit', onExit); resolve(false); }, 500);
      const onExit = () => { clearTimeout(t); resolve(true); };
      child.once('exit', onExit);
    });
    if (!exited) {
      child.kill('SIGKILL');
      await new Promise(resolve => {
        if (child.exitCode !== null || child.signalCode !== null) { resolve(); return; }
        const t = setTimeout(resolve, 500);
        child.once('exit', () => { clearTimeout(t); resolve(); });
      });
    }
    try { fs.rmSync(tmpHome, { recursive: true, force: true }); } catch {}
  }
}

const post = (PORT, token, urlPath, body) => fetch(`http://127.0.0.1:${PORT}${urlPath}`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
  body: JSON.stringify(body),
});

test('/permission-request returns 200 (NOTIFY_DISABLED path, regression for inline_keyboard wiring)', async () => {
  await withRouter({}, async ({ PORT, token }) => {
    const r = await post(PORT, token, '/permission-request', {
      tool_name: 'Bash',
      description: 'rm -rf /',
      prefixed_request_id: 'abc123-defgh',
    });
    assert.equal(r.status, 200, '/permission-request must return 200 even when Telegram is disabled');
    const j = await r.json();
    assert.equal(j.ok, true);
  });
});

test('/permission-request returns 400 on malformed prefixed_request_id (Codex Medium)', async () => {
  // Canonical-shape rejection: missing dash, wrong charset, padding, empty.
  // The route must short-circuit BEFORE any tgSend call so an upstream caller
  // gets an actionable error instead of silently dropping the verdict path.
  await withRouter({}, async ({ PORT, token }) => {
    for (const bad of ['abc123defgh', 'ABC123-defgh', 'abc123-deflg', '', 'abc123-defgh ', 'x']) {
      const r = await post(PORT, token, '/permission-request', {
        tool_name: 'Bash',
        description: 'something',
        prefixed_request_id: bad,
      });
      assert.equal(r.status, 400, `bad prefixed_request_id ${JSON.stringify(bad)} must reject with 400`);
      const j = await r.json();
      assert.match(j.error, /invalid prefixed_request_id/);
    }
  });
});
