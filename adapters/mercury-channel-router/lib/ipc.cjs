'use strict';

// HTTP server + IPC auth token + singleton lock-file management (#303 split
// from router.cjs). createServer() is plumbing-only — behavior callbacks
// (deriveLabel/scheduleShutdown/tgSend/htmlEsc) are injected by router.cjs.

const http = require('http');
const fs = require('fs');
const path = require('path');
const state = require('./state.cjs');
const { bodyOf, MAX_BODY_BYTES } = require('./body.cjs');
const { buildVerdictKeyboard, buildPermissionRequestText, isValidPrefixedRequestId } = require('./callback.cjs');

const { TAG, TOKEN, TOKEN_FILE, LOCK_FILE, MAX_SESS } = state;

// Issue #304: lock acquisition retry budget.
const LOCK_ACQUIRE_RETRIES = 3;

const writeToken = () => {
  try { fs.mkdirSync(path.dirname(TOKEN_FILE), { recursive: true }); fs.writeFileSync(TOKEN_FILE, TOKEN, { mode: 0o600 }); }
  catch (e) { process.stderr.write(`${TAG} token write error: ${e.message}\n`); }
};
const cleanupToken = () => { try { fs.unlinkSync(TOKEN_FILE); } catch {} };

// Lock file — atomic O_CREAT|O_EXCL; fail-closed on EEXIST (lock contention,
// not socket EADDRINUSE — Copilot iter-1 C2). #304 nit 4: acquireLock returns
// true/false so callers gate `writeToken()` / `initBot()` on the bool and a
// silent FS failure does not let Telegram polling start without singleton
// ownership (re-opening the 409 race). Live-PID and EPERM paths still
// `process.exit(1)` directly. safeUnlinkLock guards stale-lock cleanup
// against ENOENT (TOCTOU) and EPERM (Windows zombie lock) — Copilot iter-1 C1.
const safeUnlinkLock = () => {
  try { fs.unlinkSync(LOCK_FILE); }
  catch (e) {
    if (e && e.code !== 'ENOENT') {
      process.stderr.write(`${TAG} stale-lock unlink failed: ${e.message}\n`);
    }
  }
};
function acquireLock() {
  fs.mkdirSync(path.dirname(LOCK_FILE), { recursive: true });
  for (let i = 0; i < LOCK_ACQUIRE_RETRIES; i++) {
    try {
      const fd = fs.openSync(LOCK_FILE, 'wx');
      fs.writeSync(fd, String(process.pid));
      fs.closeSync(fd);
      return true;
    } catch (e) {
      if (e.code !== 'EEXIST') { process.stderr.write(`${TAG} lock error: ${e.message}\n`); return false; }
      try {
        const pid = parseInt(fs.readFileSync(LOCK_FILE, 'utf8').trim(), 10);
        if (pid && pid !== process.pid) {
          try {
            process.kill(pid, 0);
            process.stderr.write(`${TAG} already running (pid ${pid})\n`);
            process.exit(1);
          }
          catch (e2) {
            if (e2.code === 'EPERM') {
              process.stderr.write(`${TAG} pid ${pid} exists (no permission); aborting\n`);
              process.exit(1);
            }
            safeUnlinkLock(); // ESRCH/other → stale, retry
          }
        } else { safeUnlinkLock(); }
      } catch { safeUnlinkLock(); }
    }
  }
  process.stderr.write(`${TAG} lock acquisition failed after ${LOCK_ACQUIRE_RETRIES} retries\n`);
  return false;
}
function releaseLock() {
  try {
    const pid = parseInt(fs.readFileSync(LOCK_FILE, 'utf8').trim(), 10);
    if (pid === process.pid) fs.unlinkSync(LOCK_FILE);
  } catch {}
}

// JSON response helper. bodyOf lives in body.cjs (#407 F1 split).
const json = (res, code, obj) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(obj)); };

// Factory — http.Server with routes wired against caller-supplied behavior
// callbacks (deriveLabel/scheduleShutdown → routing.cjs, tgSend/htmlEsc →
// telegram.cjs). Keeps this module free of telegram/routing imports.
function createServer({ deriveLabel, scheduleShutdown, tgSend, htmlEsc }) {
  return http.createServer(async (req, res) => {
    const url = req.url || '/';
    const m = req.method || 'GET';
    if (m === 'GET' && url === '/health') {
      return json(res, 200, { ok: true, sessions: state.sessions.size, uptime: Date.now() - state.startTs });
    }
    if (req.headers.authorization !== `Bearer ${TOKEN}`) return json(res, 401, { error: 'unauthorized' });
    if (m === 'GET' && url === '/sessions') {
      return json(res, 200, [...state.sessions.values()].map(({ id, label, branch, pid, sseClients }) => ({
        id, label, branch, pid, active: id === state.activeId, subscribers: (sseClients || []).length,
      })));
    }
    if (m === 'GET' && url.startsWith('/inbox/')) {
      const sid = url.slice(7);
      if (!state.sessions.has(sid)) return json(res, 404, { error: 'session not found' });
      res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' });
      res.write('data: {"type":"connected"}\n\n');
      // Issue #297: mutate the underlying array in place (splice) rather than
      // reassigning a filtered copy, so that the disconnect cleanup survives a
      // subsequent /register upsert. After upsert, sessions.get(sid).sseClients
      // is the SAME array reference (preserved from existing). Reassigning
      // `s.sseClients = ...filter()` would only update the captured (now-stale)
      // session object's property, leaving the live Map entry holding a dead
      // ServerResponse forever.
      const s = state.sessions.get(sid);
      s.sseClients = s.sseClients || [];
      s.sseClients.push(res);
      const sseArr = s.sseClients;
      req.on('close', () => { const i = sseArr.indexOf(res); if (i !== -1) sseArr.splice(i, 1); });
      return;
    }
    if (m === 'POST' && url === '/register') {
      let body; try { body = await bodyOf(req, res); } catch { if (res.writableEnded) return; return json(res, 400, { error: 'bad json' }); }
      // Argus #297 iter-1: session_id is an externally-supplied identifier that
      // flows into stderr logs and Map keys. Restrict to a controlled charset
      // and length to defend against log injection and unbounded memory growth
      // from malformed bodies. Accepts the existing client format
      // `cc-<pid>-<base36>` plus arbitrary CLAUDE_SESSION_ID values from
      // upstream — those are dash-separated lowercase hex (uuid-like).
      const session_id = String(body.session_id || '').trim();
      if (!/^[A-Za-z0-9._:-]{1,128}$/.test(session_id)) return json(res, 400, { error: 'invalid session_id' });
      const { project_path, branch, pid, short_id } = body;
      // Issue #297: /register is an upsert. Existing session_id → update fields
      // in place and preserve `sseClients` so the live SSE connection from
      // connectInbox() is not orphaned. The MAX_SESS cap only applies to NEW
      // sessions; updating an existing session must not be rejected.
      const existing = state.sessions.get(session_id);
      if (!existing && state.sessions.size >= MAX_SESS) return json(res, 429, { error: 'session limit reached', max: MAX_SESS });
      const label = body.label || deriveLabel({ project_path, branch });
      state.sessions.set(session_id, {
        id: session_id, label, project_path, branch, pid,
        shortId: short_id || session_id.slice(0, 6),
        sseClients: existing ? existing.sseClients : [],
      });
      if (!state.activeId) state.activeId = session_id;
      clearTimeout(state.shutdownTimer); state.shutdownTimer = null;
      process.stderr.write(`${TAG} ${existing ? 'updated' : 'registered'} ${session_id} [${label}]\n`);
      return json(res, 200, { ok: true, label, active: state.activeId === session_id, updated: !!existing });
    }
    if (m === 'DELETE' && url.startsWith('/register/')) {
      const sid = url.slice(10);
      state.sessions.delete(sid);
      if (state.activeId === sid) state.activeId = state.sessions.size > 0 ? state.sessions.keys().next().value : null;
      process.stderr.write(`${TAG} deregistered ${sid}\n`);
      scheduleShutdown();
      return json(res, 200, { ok: true });
    }
    if (m === 'POST' && url.startsWith('/take-ownership/')) {
      const sid = url.slice(16);
      if (!state.sessions.has(sid)) return json(res, 404, { error: 'session not found' });
      state.activeId = sid;
      return json(res, 200, { ok: true });
    }
    if (m === 'POST' && url === '/notify') {
      let body; try { body = await bodyOf(req, res); } catch { if (res.writableEnded) return; return json(res, 400, { error: 'bad json' }); }
      const { severity = 'info', title = '', body: mb = '', label: fl } = body;
      const lbl = fl || (state.sessions.get(state.activeId)?.label) || 'mercury';
      const chatId = state.lastChatId || (process.env.MERCURY_TELEGRAM_CHAT_ID ? Number(process.env.MERCURY_TELEGRAM_CHAT_ID) : null);
      if (chatId) await tgSend(chatId, `[${htmlEsc(lbl)}] <b>${htmlEsc(String(severity).toUpperCase())}: ${htmlEsc(title)}</b>\n${htmlEsc(mb)}`);
      return json(res, 200, { ok: true });
    }
    if (m === 'POST' && url === '/reply') {
      let body; try { body = await bodyOf(req, res); } catch { if (res.writableEnded) return; return json(res, 400, { error: 'bad json' }); }
      // Issue #407 F3: strict typing on external IPC boundary — pre-#407 truthy
      // check accepted 0/booleans/arrays/whitespace, surfacing opaque tgSend errors.
      // isSafeInteger (not isInteger) rejects precision-lossy ids above 2^53-1,
      // belt-and-braces against IPC callers that build chat_id from a string
      // (Codex Low). Telegram channels use negative ids — both still pass.
      const { chat_id, text, label } = body;
      if (!Number.isSafeInteger(chat_id) || typeof text !== 'string' || !text.trim()) {
        return json(res, 400, { error: 'chat_id must be safe integer; text must be non-empty string' });
      }
      const s = [...state.sessions.values()].find(x => x.id === body.session_id) || state.sessions.get(state.activeId);
      await tgSend(chat_id, `[${htmlEsc(label || (s?.label) || 'mercury')}] ${htmlEsc(text)}`);
      return json(res, 200, { ok: true });
    }
    if (m === 'POST' && url === '/permission-request') {
      let body; try { body = await bodyOf(req, res); } catch { if (res.writableEnded) return; return json(res, 400, { error: 'bad json' }); }
      const { tool_name = '', description = '', prefixed_request_id = '' } = body;
      // Codex audit (Medium): reject malformed prefixed_request_id at the IPC boundary; same shape routing.cjs verdict regex enforces.
      if (!isValidPrefixedRequestId(prefixed_request_id)) return json(res, 400, { error: 'invalid prefixed_request_id; expected pattern: <6 lowercase alphanumeric>-<5 lowercase letters except "l">' });
      const chatId = state.lastChatId || (process.env.MERCURY_TELEGRAM_CHAT_ID ? Number(process.env.MERCURY_TELEGRAM_CHAT_ID) : null);
      if (chatId) await tgSend(chatId, buildPermissionRequestText(tool_name, description, prefixed_request_id, htmlEsc), { reply_markup: buildVerdictKeyboard(prefixed_request_id) });
      return json(res, 200, { ok: true });
    }
    json(res, 404, { error: 'not found' });
  });
}

module.exports = {
  writeToken, cleanupToken, safeUnlinkLock, acquireLock, releaseLock,
  createServer, LOCK_ACQUIRE_RETRIES, MAX_BODY_BYTES,
};
