'use strict';

// hardening-407.test.cjs — Issue #407 (notify-hub follow-up) regressions for
// the four pre-existing-logic mitigations Argus flagged on PR #406:
//   F1: bodyOf max-body cap (lib/body.cjs)        — behavioral 413 + env override
//   F2: @<label> shorthand uses resolveLane       — structural (routing.cjs)
//   F3: /reply chat_id integer + text non-empty   — behavioral 400
//   F4: client drops input_preview IPC payload    — structural (mcp-tools.cjs)
//
// F1 and F3 spawn the real router and exercise the HTTP boundary — that is the
// only place the size cap and validation actually live, and matches the test
// pattern from register-upsert.test.cjs. F2 and F4 are source-level structural
// assertions: behavioral coverage for F2 (Telegram message dispatch path) would
// require booting grammy under test, which the existing suite intentionally
// avoids (see housekeeping-304.test.cjs:11-14 rationale). The structural check
// is sufficient to catch a regression that swaps resolveLane back to startsWith.

const { test } = require('node:test');
const assert  = require('node:assert/strict');
const { spawn } = require('child_process');
const path = require('path');
const fs   = require('fs');
const os   = require('os');

const ROUTER = path.resolve(__dirname, '..', 'router.cjs');

function pickPort() { return 19000 + Math.floor(Math.random() * 1000); }

// Spawn the router with an isolated HOME (so token/lock files do not collide
// with the production router) and an optional env overlay. Mirrors the helper
// in register-upsert.test.cjs — duplicated here so #407 stays self-contained.
async function withRouter(extraEnv, body) {
  const PORT = pickPort();
  const tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'mcr-407-'));
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
    if (!exited) child.kill('SIGKILL');
    try { fs.rmSync(tmpHome, { recursive: true, force: true }); } catch {}
  }
}

const postRaw = (PORT, token, urlPath, body, contentType = 'application/json') =>
  fetch(`http://127.0.0.1:${PORT}${urlPath}`, {
    method: 'POST',
    headers: { 'Content-Type': contentType, Authorization: `Bearer ${token}` },
    body,
  });

// ─── F1: bodyOf max-body cap ─────────────────────────────────────────────────

test('F1: POST body over MAX_BODY_BYTES returns 413 (default 64 KB cap)', async () => {
  await withRouter({}, async ({ PORT, token }) => {
    // 65 KB of JSON-shaped payload > 64 KB default cap.
    const big = 'x'.repeat(65 * 1024);
    const body = JSON.stringify({ session_id: 'sid', project_path: '/p', branch: 'b', pid: 1, short_id: 'aaaaaa', filler: big });
    const r = await postRaw(PORT, token, '/register', body);
    assert.equal(r.status, 413, 'oversized body must be rejected with 413');
    const j = await r.json();
    assert.equal(j.error, 'request body too large');
    assert.equal(typeof j.max, 'number');
  });
});

test('F1: MERCURY_ROUTER_MAX_BODY_BYTES env override is honored', async () => {
  // Set a tiny cap; even a small valid body should now overflow.
  await withRouter({ MERCURY_ROUTER_MAX_BODY_BYTES: '200' }, async ({ PORT, token }) => {
    const body = JSON.stringify({ session_id: 'sid', project_path: '/p', branch: 'b', pid: 1, short_id: 'aaaaaa', filler: 'x'.repeat(300) });
    const r = await postRaw(PORT, token, '/register', body);
    assert.equal(r.status, 413);
    const j = await r.json();
    assert.equal(j.max, 200, 'env override must set the documented cap');
  });
});

test('F1: small body under cap still routes normally', async () => {
  await withRouter({}, async ({ PORT, token }) => {
    const body = JSON.stringify({ session_id: 'sid-ok', project_path: '/p', branch: 'b', pid: 1, short_id: 'okokok' });
    const r = await postRaw(PORT, token, '/register', body);
    assert.equal(r.status, 200, 'normal payload must not be affected by the cap');
  });
});

// ─── F3: /reply chat_id + text validation ────────────────────────────────────

test('F3: /reply rejects non-integer chat_id with 400', async () => {
  await withRouter({}, async ({ PORT, token }) => {
    for (const bad of [{ chat_id: 'abc',  text: 'hi' },
                       { chat_id: 12.5,   text: 'hi' },
                       { chat_id: true,   text: 'hi' },
                       { chat_id: null,   text: 'hi' }]) {
      const r = await postRaw(PORT, token, '/reply', JSON.stringify(bad));
      assert.equal(r.status, 400, `chat_id=${JSON.stringify(bad.chat_id)} must be rejected`);
    }
  });
});

test('F3: /reply rejects non-string or empty/whitespace text with 400', async () => {
  await withRouter({}, async ({ PORT, token }) => {
    for (const bad of [{ chat_id: 123, text: '' },
                       { chat_id: 123, text: '   ' },
                       { chat_id: 123, text: '\t\n' },
                       { chat_id: 123, text: 42 },
                       { chat_id: 123, text: null }]) {
      const r = await postRaw(PORT, token, '/reply', JSON.stringify(bad));
      assert.equal(r.status, 400, `text=${JSON.stringify(bad.text)} must be rejected`);
      const j = await r.json();
      assert.match(j.error, /chat_id.*safe integer.*text.*non-empty/);
    }
  });
});

test('F3: /reply rejects precision-lossy chat_id with 400 (Number.isSafeInteger guard)', async () => {
  // Codex Low: isInteger accepts 2^53 + 1; isSafeInteger does not. Cheap
  // defense-in-depth against IPC callers that build chat_id from a string.
  await withRouter({}, async ({ PORT, token }) => {
    const r = await postRaw(PORT, token, '/reply', JSON.stringify({ chat_id: Number.MAX_SAFE_INTEGER + 1, text: 'hi' }));
    assert.equal(r.status, 400, 'chat_id above Number.MAX_SAFE_INTEGER must be rejected');
  });
});

test('F3: /reply accepts valid integer chat_id + non-empty text (no Telegram chat known → still 200)', async () => {
  // No active session + MERCURY_NOTIFY_DISABLED=1 means tgSend is a no-op,
  // but the validation path must still return 200 for a well-formed body.
  // Cover positive AND negative (Telegram channels use negative ids).
  await withRouter({}, async ({ PORT, token }) => {
    for (const cid of [12345, -1001234567890]) {
      const r = await postRaw(PORT, token, '/reply', JSON.stringify({ chat_id: cid, text: 'hello' }));
      assert.equal(r.status, 200, `valid chat_id=${cid} must pass validation`);
    }
  });
});

// ─── F2: structural — @<label> plain-text uses resolveLane ───────────────────

test('F2: routeMessage uses resolveLane for plain @label shorthand', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'routing.cjs'), 'utf8');
  // Strip line comments so a future "// uses startsWith historically" comment
  // does not get caught by the negative match below.
  const code = src.split('\n').map(l => l.replace(/\/\/[^\n]*$/, '')).join('\n');
  // Locate routeMessage and bound the search to its body (until next top-level
  // declaration). routeMessage is the last named function in routing.cjs.
  const idx = code.indexOf('async function routeMessage');
  assert.ok(idx >= 0, 'routeMessage must exist in routing.cjs');
  const body = code.slice(idx);
  // Pre-#407: `state.sessions.values()].find(s => s.label.startsWith(pM[1]))`
  // Post-#407: `resolveLane(chatId, pM[1].replace(/^#/, ''))`
  assert.match(body, /resolveLane\(chatId,\s*pM\[1\]/,
    'routeMessage @label shorthand must dispatch through resolveLane');
  // The old startsWith pattern must not survive on the pM[1] path.
  assert.doesNotMatch(body, /find\(s\s*=>\s*s\.label\.startsWith\(pM\[1\]\)\)/,
    'legacy startsWith auto-pick on @label shorthand must be gone');
});

// ─── F4: structural — client drops input_preview from IPC payload ────────────

test('F4: client mcp-tools.cjs no longer forwards input_preview to router', () => {
  const clientSrc = fs.readFileSync(
    path.join(__dirname, '..', '..', 'mercury-channel-client', 'lib', 'mcp-tools.cjs'),
    'utf8',
  );
  // Strip line comments — the file legitimately references input_preview in
  // its #407 F4 explanation block; what matters is no code path serializes it.
  const code = clientSrc.split('\n').map(l => l.replace(/\/\/[^\n]*$/, '')).join('\n');
  assert.doesNotMatch(code, /input_preview/,
    'client must not destructure or forward input_preview after #407 F4');
});

// ─── body.cjs sanity: MAX_BODY_BYTES env parsing covers garbage ──────────────

test('body.cjs: MAX_BODY_BYTES parses env, falls back to 64 KB on garbage; clamps oversize', () => {
  // Spawn a tiny harness that imports body.cjs with assorted env values and
  // prints the resolved cap. Doing it in-process would not work — body.cjs
  // reads process.env at require time and gets cached.
  const MAX_CEIL = 16 * 1024 * 1024;
  const cases = [
    { env: undefined, expect: 65536 },
    { env: 'abc',     expect: 65536 },
    { env: '-100',    expect: 65536 },
    { env: '0',       expect: 65536 },
    { env: '1',       expect: 1 },
    { env: '128.7',   expect: 128 },
    { env: '1000000', expect: 1000000 },
    { env: 'Infinity', expect: 65536 },                                       // non-finite → fallback
    { env: '1e308',   expect: MAX_CEIL },                                     // Claude Low: oversize clamp
    { env: String(MAX_CEIL + 1), expect: MAX_CEIL },                          // ceiling exact
  ];
  for (const { env, expect } of cases) {
    const cleanEnv = { ...process.env };
    delete cleanEnv.MERCURY_ROUTER_MAX_BODY_BYTES;
    if (env !== undefined) cleanEnv.MERCURY_ROUTER_MAX_BODY_BYTES = env;
    const out = require('child_process').spawnSync(
      process.execPath,
      ['-e', `console.log(require(${JSON.stringify(path.join(__dirname, '..', 'lib', 'body.cjs'))}).MAX_BODY_BYTES)`],
      { env: cleanEnv, encoding: 'utf8' },
    );
    assert.equal(Number(out.stdout.trim()), expect,
      `MERCURY_ROUTER_MAX_BODY_BYTES=${JSON.stringify(env)} expected ${expect}, got ${out.stdout.trim()}`);
  }
});

// ─── body.cjs: Buffer.concat preserves multi-byte UTF-8 across chunk boundaries ──

test('body.cjs: 4-byte emoji split across TCP frames decodes cleanly (Claude dual-verify Medium)', async () => {
  // Pre-#407 (and pre-Claude-Medium-fix) the per-chunk `b += c` would have
  // emitted U+FFFD replacement characters when a multi-byte codepoint was
  // split across two `data` events. Buffer.concat at end-of-stream avoids it.
  // We spin up the real router and POST a body whose payload includes a
  // 4-byte emoji. The body is small enough that node typically delivers it
  // as one chunk on localhost — to actually exercise the split, we send the
  // body in two halves manually via raw `net` socket, splitting between
  // bytes 2 and 3 of the emoji's UTF-8 encoding.
  await withRouter({}, async ({ PORT, token }) => {
    const net = require('net');
    const emoji = '😀'; // U+1F600 → 0xF0 0x9F 0x98 0x80 (4 bytes)
    const body = `{"session_id":"emoji-test","project_path":"/p","branch":"b","pid":1,"short_id":"emojis","label":"${emoji}"}`;
    const buf = Buffer.from(body, 'utf8');
    // Find the emoji's first byte (0xF0) and split between its 2nd + 3rd bytes.
    const emojiStart = buf.indexOf(0xF0);
    assert.ok(emojiStart >= 0, 'sanity: emoji must be present in test fixture');
    const split = emojiStart + 2;
    const head = buf.subarray(0, split);
    const tail = buf.subarray(split);

    const headers =
      'POST /register HTTP/1.1\r\n' +
      'Host: 127.0.0.1\r\n' +
      `Authorization: Bearer ${token}\r\n` +
      'Content-Type: application/json\r\n' +
      `Content-Length: ${buf.length}\r\n` +
      'Connection: close\r\n\r\n';

    const statusLine = await new Promise((resolve, reject) => {
      const sock = net.connect(PORT, '127.0.0.1', () => {
        sock.write(headers);
        sock.write(head);
        // Give the server a tick to consume the first chunk so the emoji
        // boundary is exercised across two `data` events.
        setTimeout(() => sock.write(tail), 50);
      });
      let raw = '';
      sock.on('data', c => { raw += c.toString('utf8'); });
      sock.on('end', () => resolve(raw.split('\r\n', 1)[0] || ''));
      sock.on('error', reject);
    });
    // We only need the status code; parsing the chunked body is fragile and
    // not the point of this test. /sessions below is the actual assertion.
    assert.match(statusLine, /^HTTP\/1\.1 200 /, `split-chunk register must return 200, got: ${statusLine}`);

    // The label round-trips back via /sessions — must contain the original emoji,
    // not U+FFFD replacement chars. If body.cjs had used `b += c` per-chunk,
    // the boundary cut between bytes 2 and 3 of the emoji would have produced
    // mojibake in the label.
    const r = await fetch(`http://127.0.0.1:${PORT}/sessions`, { headers: { Authorization: `Bearer ${token}` } });
    const sess = await r.json();
    const e = sess.find(s => s.id === 'emoji-test');
    assert.ok(e, 'session must be registered');
    assert.equal(e.label, emoji, `label must round-trip cleanly; got ${JSON.stringify(e.label)}`);
    assert.ok(!e.label.includes('�'), 'no U+FFFD replacement chars allowed');
  });
});
