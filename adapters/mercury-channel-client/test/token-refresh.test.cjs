'use strict';
// token-refresh.test.cjs — Unit tests for lib/token-refresh.cjs (Issue #301).
// Uses node:test (built-in, no external deps).
//
// Run: node --test adapters/mercury-channel-client/test/token-refresh.test.cjs

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { routerFetchWithRetry } = require('../lib/token-refresh.cjs');

// Build a deterministic getToken/invalidateToken pair backed by a mutable cell.
// Mirrors channel.cjs's actual `_token` cache: first call materializes from
// the "file" (the `read()` callback), subsequent calls return cached, and
// invalidateToken() clears so the next getToken() re-reads.
function makeTokenStore(read) {
  let cached = null;
  return {
    getToken: async () => (cached !== null ? cached : (cached = await read())),
    invalidateToken: () => { cached = null; },
    peek: () => cached,
  };
}

function jsonRes(status, body = {}) {
  return { status, ok: status >= 200 && status < 300, json: async () => body };
}

test('200 response: no invalidation, no retry', async () => {
  const store = makeTokenStore(async () => 'token-A');
  let calls = 0;
  const fetchImpl = async () => { calls++; return jsonRes(200, { ok: true }); };
  const res = await routerFetchWithRetry({
    url: 'http://127.0.0.1:8788/health', opts: {},
    getToken: store.getToken, invalidateToken: store.invalidateToken, fetchImpl,
  });
  assert.equal(res.status, 200);
  assert.equal(calls, 1);
  assert.equal(store.peek(), 'token-A'); // still cached
});

test('429 / 500: bypass invalidation, no retry (only 401 triggers)', async () => {
  const store = makeTokenStore(async () => 'token-A');
  for (const status of [400, 404, 429, 500, 502, 503]) {
    let calls = 0;
    const fetchImpl = async () => { calls++; return jsonRes(status); };
    const res = await routerFetchWithRetry({
      url: 'http://127.0.0.1:8788/x', opts: {},
      getToken: store.getToken, invalidateToken: store.invalidateToken, fetchImpl,
    });
    assert.equal(res.status, status);
    assert.equal(calls, 1, `status ${status} should not retry`);
    assert.equal(store.peek(), 'token-A', `status ${status} should not invalidate`);
  }
});

test('401 then 200: invalidate + retry once with fresh token', async () => {
  // Simulates router restart: first read returns stale token, second read
  // returns fresh token after the cache was invalidated.
  const tokens = ['stale', 'fresh'];
  let readIdx = 0;
  const store = makeTokenStore(async () => tokens[readIdx++]);

  const seen = [];
  const fetchImpl = async (url, init) => {
    seen.push(init.headers['Authorization']);
    return jsonRes(seen.length === 1 ? 401 : 200);
  };
  const res = await routerFetchWithRetry({
    url: 'http://127.0.0.1:8788/register', opts: { method: 'POST' },
    getToken: store.getToken, invalidateToken: store.invalidateToken, fetchImpl,
  });
  assert.equal(res.status, 200);
  assert.equal(seen.length, 2);
  assert.equal(seen[0], 'Bearer stale');
  assert.equal(seen[1], 'Bearer fresh');
  assert.equal(store.peek(), 'fresh'); // cached after retry
});

test('401 twice: invalidate ONCE, retry ONCE, propagate second 401', async () => {
  // Retry-once cap: a write race or router auth bug must not loop.
  const tokens = ['t1', 't2'];
  let readIdx = 0;
  const store = makeTokenStore(async () => tokens[readIdx++]);
  let invalidations = 0;
  const wrapped = {
    getToken: store.getToken,
    invalidateToken: () => { invalidations++; store.invalidateToken(); },
  };

  let calls = 0;
  const fetchImpl = async () => { calls++; return jsonRes(401); };
  const res = await routerFetchWithRetry({
    url: 'http://127.0.0.1:8788/reply', opts: {},
    getToken: wrapped.getToken, invalidateToken: wrapped.invalidateToken, fetchImpl,
  });
  assert.equal(res.status, 401);
  assert.equal(calls, 2, 'exactly 2 attempts');
  assert.equal(invalidations, 1, 'invalidate called exactly once');
});

test('opts.method + opts.body forwarded to both attempts', async () => {
  const store = makeTokenStore(async () => 'tk');
  const seen = [];
  const fetchImpl = async (url, init) => {
    seen.push({ method: init.method, body: init.body });
    return jsonRes(seen.length === 1 ? 401 : 200);
  };
  await routerFetchWithRetry({
    url: 'http://127.0.0.1:8788/notify',
    opts: { method: 'POST', body: '{"x":1}', headers: { 'Content-Type': 'application/json' } },
    getToken: store.getToken, invalidateToken: store.invalidateToken, fetchImpl,
  });
  assert.equal(seen.length, 2);
  assert.deepEqual(seen[0], { method: 'POST', body: '{"x":1}' });
  assert.deepEqual(seen[1], { method: 'POST', body: '{"x":1}' });
});

test('caller-supplied Content-Type preserved alongside Bearer header', async () => {
  const store = makeTokenStore(async () => 'tk');
  let seenHeaders = null;
  const fetchImpl = async (url, init) => { seenHeaders = init.headers; return jsonRes(200); };
  await routerFetchWithRetry({
    url: 'http://127.0.0.1:8788/x',
    opts: { headers: { 'Content-Type': 'application/json', 'X-Custom': 'v' } },
    getToken: store.getToken, invalidateToken: store.invalidateToken, fetchImpl,
  });
  assert.equal(seenHeaders['Content-Type'], 'application/json');
  assert.equal(seenHeaders['X-Custom'], 'v');
  assert.equal(seenHeaders['Authorization'], 'Bearer tk');
});

test('getToken returning null: Authorization header omitted', async () => {
  // Mirrors the cold-start case before router has written the token file.
  const store = makeTokenStore(async () => null);
  let seenHeaders = null;
  const fetchImpl = async (url, init) => { seenHeaders = init.headers; return jsonRes(200); };
  await routerFetchWithRetry({
    url: 'http://127.0.0.1:8788/health', opts: {},
    getToken: store.getToken, invalidateToken: store.invalidateToken, fetchImpl,
  });
  assert.equal(seenHeaders['Authorization'], undefined);
});

test('timeoutMs default 3000 applied via AbortSignal', async () => {
  // Smoke test that the helper passes through a signal — we don't probe the
  // timeout duration (would slow the suite), just confirm `init.signal`
  // is wired and is an AbortSignal instance.
  const store = makeTokenStore(async () => 'tk');
  let sawSignal = null;
  const fetchImpl = async (url, init) => { sawSignal = init.signal; return jsonRes(200); };
  await routerFetchWithRetry({
    url: 'http://127.0.0.1:8788/x', opts: {},
    getToken: store.getToken, invalidateToken: store.invalidateToken, fetchImpl,
  });
  assert.ok(sawSignal instanceof AbortSignal);
});

test('opts.signal override wins over default timeout (existing semantics)', async () => {
  // routerFetch currently lets callers override the timeout by passing
  // their own signal. The retry-helper preserves this — the explicit
  // timeout is a floor, not a ceiling. (Test the documented behavior.)
  const store = makeTokenStore(async () => 'tk');
  const customSignal = AbortSignal.timeout(10_000);
  let sawSignal = null;
  const fetchImpl = async (url, init) => { sawSignal = init.signal; return jsonRes(200); };
  await routerFetchWithRetry({
    url: 'http://127.0.0.1:8788/x', opts: { signal: customSignal },
    getToken: store.getToken, invalidateToken: store.invalidateToken, fetchImpl,
  });
  assert.equal(sawSignal, customSignal);
});
