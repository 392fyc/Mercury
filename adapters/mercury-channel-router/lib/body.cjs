'use strict';

// Issue #407 F1: per-request body parser with byte cap, split from ipc.cjs to
// stay under the #303 ≤200 LOC contract. bodyOf writes 413 + destroys the
// socket on overflow; caller checks res.writableEnded in catch to skip the
// fall-through 400. Buffer.concat at end-of-stream is mandatory: per-chunk
// `String += buf` would coerce each chunk independently, emitting U+FFFD for
// any UTF-8 codepoint split across two TCP frames (Claude dual-verify Medium).
// Upper-clamp on MAX_BODY_BYTES prevents a misconfigured 1e308 env from
// silently disabling the cap (Claude dual-verify Low). isSafeInteger is not
// applicable here — chat_ids are out of scope; this is bytes.

const _MBB_MAX = 16 * 1024 * 1024;                                                            // 16 MB hard ceiling
const _MBB = Number(process.env.MERCURY_ROUTER_MAX_BODY_BYTES);
const MAX_BODY_BYTES = Number.isFinite(_MBB) && _MBB >= 1
  ? Math.min(Math.floor(_MBB), _MBB_MAX) : 65536;

const writeJson = (res, code, obj) => {
  res.writeHead(code, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(obj));
};

const bodyOf = (req, res) => new Promise((ok, fail) => {
  const chunks = [];
  let bytes = 0;
  let settled = false;
  // settled flag + listener teardown is Argus iter-2 hardening: prevents
  // duplicate ok/fail on a hostile client that keeps streaming after the
  // overflow + 413 (CPU amplification) and prevents the Promise from
  // hanging if the client aborts mid-stream before `end` fires.
  const done = (val, err) => {
    if (settled) return;
    settled = true;
    try { req.removeAllListeners('data'); req.removeAllListeners('end'); req.removeAllListeners('error'); req.removeAllListeners('close'); req.pause && req.pause(); } catch {}
    if (err) fail(err); else ok(val);
  };
  req.on('data', c => {
    bytes += c.length;
    if (bytes > MAX_BODY_BYTES) {
      // writableEnded check covers a future caller that pre-writes headers
      // before awaiting bodyOf — current ipc.cjs sites are all pre-await.
      if (res && !res.headersSent && !res.writableEnded) {
        // socket destroy in res.end callback so well-behaved clients receive
        // the 413 body (Claude dual-verify Low). Fallback destroys immediately
        // if `end` never fires (hostile client streaming forever).
        res.writeHead(413, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'request body too large', max: MAX_BODY_BYTES }), () => {
          try { req.socket && req.socket.destroy(); } catch {}
        });
      } else {
        try { req.socket && req.socket.destroy(); } catch {}
      }
      return done(undefined, Object.assign(new Error('body too large'), { code: 'BODY_TOO_LARGE' }));
    }
    chunks.push(c);
  });
  req.on('end', () => {
    try { done(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}')); }
    catch (e) { done(undefined, e); }
  });
  req.on('error', e => done(undefined, e));
  // Argus iter-2 Medium: client abort fires 'close' with !req.complete before
  // 'end' if disconnected mid-stream. Reject so callers don't hang forever.
  req.on('close', () => { if (!req.complete) done(undefined, Object.assign(new Error('client aborted'), { code: 'ABORTED' })); });
});

module.exports = { bodyOf, MAX_BODY_BYTES, writeJson };
