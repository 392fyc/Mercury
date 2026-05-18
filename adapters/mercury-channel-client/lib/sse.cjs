'use strict';

// SSE inbox consumer for mercury-channel-client. #303 split from channel.cjs.
// connectInbox() loops while state.sseActive is true; reconnects with
// exponential backoff (#299) and resets the attempt counter only on
// substantive events (#299 Codex High).

const state = require('./state.cjs');
const { nextBackoffMs, isSubstantiveEvent } = require('./backoff.cjs');
const { getToken, invalidateToken, ensureRouter } = require('./router-bootstrap.cjs');
const { mcp } = require('./mcp-tools.cjs');

const { TAG, PORT, SESSION_ID } = state;

const xmlEsc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&apos;');

function startInboxIfNeeded() {
  if (state.inboxStarted) return;
  state.inboxStarted = true;
  connectInbox().catch(e => process.stderr.write(`${TAG} inbox error: ${e.message}\n`));
}

async function connectInbox() {
  // Issue #299: exponential reconnect backoff (1s → 2s → 5s → 10s → 30s max).
  // `reconnectAttempt` is the count of consecutive *failed-or-empty*
  // connections; a connection that delivers at least one **substantive**
  // event (message/verdict/command, NOT the synthetic `connected` ping the
  // router sends on every attach) resets it so transient flaps don't
  // accumulate into a 30s wait.
  let reconnectAttempt = 0;
  while (state.sseActive) {
    let receivedEvent = false;
    try {
      const token = await getToken();
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      // no AbortSignal timeout: SSE is indefinite-lived; reconnect only on real disconnect
      const res = await fetch(`http://127.0.0.1:${PORT}/inbox/${SESSION_ID}`, { headers });
      if (!res.ok || !res.body) {
        // Issue #301: SSE goes through a bare fetch, not routerFetchWithRetry,
        // so 401 here (router restart → stale token) would otherwise loop
        // forever on the same cached token. Invalidate so the NEXT iteration's
        // getToken() re-reads ~/.mercury/router.token. The retry-once contract
        // still holds: caller can only walk the backoff schedule (1s..30s) for
        // a sustained 401 stream, not amplify it.
        if (res.status === 401) invalidateToken();
        await new Promise(r => setTimeout(r, nextBackoffMs(reconnectAttempt++)));
        continue;
      }
      const reader = res.body.getReader();
      const dec    = new TextDecoder();
      let   buf    = '';
      while (state.sseActive) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split('\n\n');
        buf = parts.pop();
        for (const part of parts) {
          const line = part.split('\n').find(l => l.startsWith('data:'));
          if (!line) continue;
          try {
            const evt = JSON.parse(line.slice(5).trim());
            // Reset backoff on first substantive event. Excludes the router's
            // synthetic `{"type":"connected"}` so a 200 + connected + EOF
            // cycle is treated as a failed reconnect, not as proof of a
            // healthy server.
            if (!receivedEvent && isSubstantiveEvent(evt)) { receivedEvent = true; reconnectAttempt = 0; }
            if (evt.type === 'message') {
              await mcp.notification({
                method: 'notifications/claude/channel',
                params: {
                  source: 'mercury-telegram',
                  label: SESSION_ID,
                  content: `<channel source="mercury-telegram" chat_id="${String(evt.from_chat).replace(/[^0-9-]/g,'')}">${xmlEsc(evt.content)}</channel>`,
                },
              });
            } else if (evt.type === 'verdict') {
              await mcp.notification({
                method: 'notifications/claude/channel/permission',
                params: { verdict: evt.verdict, request_id: evt.request_id },
              });
            } else if (evt.type === 'command') {
              // Phase C (#324): forward known payload keys (path/model/mode) as XML
              // attributes AND in body so dir/model/permission-mode commands deliver
              // the operand to the lane session, not just the bare verb.
              const chatAttr = String(evt.from_chat).replace(/[^0-9-]/g,'');
              const payloadParts = [];
              const bodyParts = [`${xmlEsc(evt.cmd)} requested by user`];
              for (const key of ['path', 'model', 'mode']) {
                if (typeof evt[key] === 'string' && evt[key].length > 0) {
                  payloadParts.push(`${key}="${xmlEsc(evt[key])}"`);
                  bodyParts.push(`${key}=${xmlEsc(evt[key])}`);
                }
              }
              const attrs = payloadParts.length ? ' ' + payloadParts.join(' ') : '';
              await mcp.notification({
                method: 'notifications/claude/channel',
                params: { source: 'mercury-telegram', label: SESSION_ID,
                  content: `<channel source="mercury-telegram" chat_id="${chatAttr}" cmd="${xmlEsc(evt.cmd)}"${attrs}>${bodyParts.join(': ')}</channel>` },
              });
            }
          } catch {}
        }
      }
      // Inner loop exited via `done: true` (normal EOF). Issue #299 / Codex
      // High: without a sleep here, the outer `while` would immediately
      // re-attach, and the router's synthetic `connected` event combined
      // with `receivedEvent`-gated reset (now only on substantive events,
      // not `connected`) would otherwise hot-loop on a 200 + connected + EOF
      // server. If this connection DID deliver a substantive event,
      // `reconnectAttempt` is already 0 so the next sleep is 1s — transient
      // flap. If it did NOT, `reconnectAttempt++` walks toward the 30s cap.
      if (state.sseActive) await new Promise(r => setTimeout(r, nextBackoffMs(reconnectAttempt++)));
    } catch {
      if (!state.sseActive) break;
      // reconnect: re-ensure router then retry. Backoff per #299: if this
      // connection delivered events `reconnectAttempt` is already 0
      // (transient blip → 1s), otherwise it increments toward 30s clamp.
      try { await ensureRouter(); } catch {}
      await new Promise(r => setTimeout(r, nextBackoffMs(reconnectAttempt++)));
    }
  }
}

module.exports = { connectInbox, startInboxIfNeeded, xmlEsc };
