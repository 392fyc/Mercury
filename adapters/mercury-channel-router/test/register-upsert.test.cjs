'use strict';
// register-upsert.test.cjs — Issue #297 H1 regression: /register must upsert
// (preserve sseClients) for an existing session_id and skip the MAX_SESS cap
// when the session_id is already registered. Uses node:test built-in.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');

const ROUTER = path.resolve(__dirname, '..', 'router.cjs');

// Pick a high port unlikely to collide with the production router (8788). Each
// test run uses a random offset so parallel runs don't fight.
function pickPort() { return 18000 + Math.floor(Math.random() * 1000); }

async function withRouter(maxSess, body) {
  const PORT = pickPort();
  // Isolate HOME so the test router writes its token/lock files into a tmp dir
  // instead of the user's real ~/.mercury (which the production router uses).
  // Override both HOME (POSIX) and USERPROFILE (Windows) since os.homedir()
  // resolves via USERPROFILE on win32.
  const tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'mcr-test-'));
  const tokenFile = path.join(tmpHome, '.mercury', 'router.token');
  const env = {
    ...process.env,
    HOME: tmpHome,
    USERPROFILE: tmpHome,
    MERCURY_ROUTER_PORT: String(PORT),
    MERCURY_ROUTER_MAX_SESS: String(maxSess),
    MERCURY_NOTIFY_DISABLED: '1',                       // skip Telegram bot init
    MERCURY_TELEGRAM_BOT_TOKEN: '',
    MERCURY_TELEGRAM_ALLOWED_USER_IDS: '',
  };
  const child = spawn(process.execPath, [ROUTER], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  let listening = false;
  child.stderr.on('data', d => { if (String(d).includes(`listening on 127.0.0.1:${PORT}`)) listening = true; });
  for (let i = 0; i < 50 && !listening; i++) await new Promise(r => setTimeout(r, 100));
  if (!listening) { child.kill('SIGKILL'); throw new Error(`router did not start on ${PORT}`); }
  // Router writes the token file after listen success. Tiny grace period.
  for (let i = 0; i < 20 && !fs.existsSync(tokenFile); i++) await new Promise(r => setTimeout(r, 50));
  const token = fs.readFileSync(tokenFile, 'utf8').trim();
  try {
    await body({ PORT, token });
  } finally {
    // Copilot finding: `child.killed` flips to true the instant kill() is
    // called, not when the process actually exits — using it as the SIGKILL
    // gate left a zombie router on test bail. Await the real `exit` event
    // (or its exitCode/signalCode equivalents) with a short timeout, then
    // escalate to SIGKILL only if the child has not actually exited.
    child.kill('SIGTERM');
    const exited = await new Promise(resolve => {
      if (child.exitCode !== null || child.signalCode !== null) { resolve(true); return; }
      const t = setTimeout(() => { child.removeListener('exit', onExit); resolve(false); }, 500);
      const onExit = () => { clearTimeout(t); resolve(true); };
      child.once('exit', onExit);
    });
    if (!exited) {
      child.kill('SIGKILL');
      // Best-effort wait so the OS releases the port before the next test.
      await new Promise(resolve => {
        if (child.exitCode !== null || child.signalCode !== null) { resolve(); return; }
        const t = setTimeout(resolve, 500);
        child.once('exit', () => { clearTimeout(t); resolve(); });
      });
    }
    try { fs.rmSync(tmpHome, { recursive: true, force: true }); } catch {}
  }
}

const post = (PORT, token, sessionBody) => fetch(`http://127.0.0.1:${PORT}/register`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
  body: JSON.stringify(sessionBody),
});

// Drain the SSE reader until we observe the marker substring or hit timeoutMs.
// HTTP chunk boundaries are not guaranteed to align with SSE event boundaries,
// so reading just the first chunk can race and produce a flaky assertion
// (Copilot finding on PR #400). Loop until we have the marker or fail loud.
async function readUntil(reader, marker, timeoutMs = 2000) {
  const dec = new TextDecoder();
  let buf = '';
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    if (buf.includes(marker)) return buf;
  }
  throw new Error(`SSE marker ${JSON.stringify(marker)} not seen within ${timeoutMs}ms; got: ${JSON.stringify(buf)}`);
}

// ─── H1 regression: upsert preserves session, returns updated=true ───────────

test('/register on existing session_id returns updated=true and preserves entry', async () => {
  await withRouter(5, async ({ PORT, token }) => {
    let r = await post(PORT, token, { session_id: 'sid-h1', project_path: '/p', branch: 'develop', pid: 1, short_id: 'aaaaaa' });
    assert.equal(r.status, 200);
    let j = await r.json();
    assert.equal(j.updated, false, 'first register is new');

    r = await post(PORT, token, { session_id: 'sid-h1', project_path: '/p', branch: 'feature/x', pid: 1, short_id: 'aaaaaa' });
    assert.equal(r.status, 200);
    j = await r.json();
    assert.equal(j.updated, true, 'second register is upsert');

    // Verify the session record has the new branch.
    r = await fetch(`http://127.0.0.1:${PORT}/sessions`, { headers: { Authorization: `Bearer ${token}` } });
    const sess = await r.json();
    assert.equal(sess.length, 1);
    assert.equal(sess[0].id, 'sid-h1');
    assert.equal(sess[0].branch, 'feature/x');
  });
});

// ─── L1 regression: MAX_SESS does not block update of existing session_id ────

test('/register MAX_SESS cap blocks new sessions but allows update of existing', async () => {
  await withRouter(2, async ({ PORT, token }) => {
    let r = await post(PORT, token, { session_id: 's1', project_path: '/a', branch: 'a', pid: 1, short_id: 'aaaaaa' });
    assert.equal(r.status, 200);
    r = await post(PORT, token, { session_id: 's2', project_path: '/b', branch: 'b', pid: 2, short_id: 'bbbbbb' });
    assert.equal(r.status, 200);

    // Third NEW session should be rejected (cap = 2).
    r = await post(PORT, token, { session_id: 's3', project_path: '/c', branch: 'c', pid: 3, short_id: 'cccccc' });
    assert.equal(r.status, 429);

    // Update of existing s1 must succeed even at cap.
    r = await post(PORT, token, { session_id: 's1', project_path: '/a', branch: 'a-updated', pid: 1, short_id: 'aaaaaa' });
    assert.equal(r.status, 200, '#297 L1: cap must not reject update of existing session_id');
    const j = await r.json();
    assert.equal(j.updated, true);
  });
});

// ─── H1 regression: sseClients survive upsert ────────────────────────────────

test('/register upsert preserves the sseClients array (#297 H1)', async () => {
  await withRouter(5, async ({ PORT, token }) => {
    let r = await post(PORT, token, { session_id: 'sid-sse', project_path: '/p', branch: 'develop', pid: 1, short_id: 'sssssa' });
    assert.equal(r.status, 200);

    // Open an SSE connection to /inbox/sid-sse and capture the connected event.
    // Use the fetch streaming API: pull until we observe the 'connected' line, then keep the reader open.
    const ac = new AbortController();
    const inboxRes = await fetch(`http://127.0.0.1:${PORT}/inbox/sid-sse`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: ac.signal,
    });
    assert.equal(inboxRes.status, 200);
    const reader = inboxRes.body.getReader();
    // Drain until we see the connected event — HTTP chunk boundaries are not
    // guaranteed to align with SSE event boundaries.
    await readUntil(reader, '"type":"connected"');

    // Give the server a tick to push res into sseClients.
    await new Promise(r => setTimeout(r, 100));

    // Re-register with new branch. If upsert is broken (replaces with sseClients:[]),
    // the SSE list is orphaned. We verify by checking /sessions subscribers count.
    r = await post(PORT, token, { session_id: 'sid-sse', project_path: '/p', branch: 'updated', pid: 1, short_id: 'sssssa' });
    assert.equal(r.status, 200);

    r = await fetch(`http://127.0.0.1:${PORT}/sessions`, { headers: { Authorization: `Bearer ${token}` } });
    const sess = await r.json();
    const s = sess.find(x => x.id === 'sid-sse');
    assert.equal(s.subscribers, 1, '#297 H1: SSE subscribers must survive upsert');

    // Cleanup.
    ac.abort();
    try { await reader.cancel(); } catch {}
  });
});

// ─── R2-M2 regression: disconnect AFTER upsert cleans the live Map array ─────

test('/inbox close handler cleans sseClients after a prior /register upsert (#297 R2-M2)', async () => {
  await withRouter(5, async ({ PORT, token }) => {
    let r = await post(PORT, token, { session_id: 'sid-cleanup', project_path: '/p', branch: 'develop', pid: 1, short_id: 'cuuuup' });
    assert.equal(r.status, 200);

    // Open SSE.
    const ac = new AbortController();
    const inboxRes = await fetch(`http://127.0.0.1:${PORT}/inbox/sid-cleanup`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: ac.signal,
    });
    assert.equal(inboxRes.status, 200);
    const reader = inboxRes.body.getReader();
    // Drain until the connected event so the SSE pipe is fully engaged
    // before we trigger the upsert race.
    await readUntil(reader, '"type":"connected"');
    await new Promise(r => setTimeout(r, 100));

    // Trigger upsert — captured `s` in the /inbox handler now diverges from
    // the live Map entry unless splice/mutation is used.
    r = await post(PORT, token, { session_id: 'sid-cleanup', project_path: '/p', branch: 'updated', pid: 1, short_id: 'cuuuup' });
    assert.equal(r.status, 200);

    // Disconnect SSE. The close handler must mutate the SHARED array so that
    // /sessions sees subscribers=0 afterwards. Pre-fix the close handler
    // reassigned `s.sseClients` on a stale object → live Map entry retained
    // the dead ServerResponse.
    ac.abort();
    try { await reader.cancel(); } catch {}
    // Give the server time to observe the disconnect + run close handler.
    await new Promise(r => setTimeout(r, 300));

    r = await fetch(`http://127.0.0.1:${PORT}/sessions`, { headers: { Authorization: `Bearer ${token}` } });
    const sess = await r.json();
    const s = sess.find(x => x.id === 'sid-cleanup');
    assert.equal(s.subscribers, 0, '#297 R2-M2: disconnect cleanup must update live Map entry after upsert');
  });
});
