'use strict';
// backoff.test.cjs — Unit tests for lib/backoff.cjs (Issue #299).
// Uses node:test (built-in, no external deps).
//
// Run: node --test adapters/mercury-channel-client/test/backoff.test.cjs

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { nextBackoffMs, SCHEDULE_MS, isSubstantiveEvent, SUBSTANTIVE_EVENT_TYPES } = require('../lib/backoff.cjs');

test('SCHEDULE_MS matches Issue #299 spec (1s, 2s, 5s, 10s, 30s)', () => {
  assert.deepEqual(SCHEDULE_MS, [1000, 2000, 5000, 10000, 30000]);
});

test('nextBackoffMs returns scheduled values for attempts 0..4', () => {
  assert.equal(nextBackoffMs(0), 1000);
  assert.equal(nextBackoffMs(1), 2000);
  assert.equal(nextBackoffMs(2), 5000);
  assert.equal(nextBackoffMs(3), 10000);
  assert.equal(nextBackoffMs(4), 30000);
});

test('nextBackoffMs clamps to 30s for attempts >= 5', () => {
  assert.equal(nextBackoffMs(5), 30000);
  assert.equal(nextBackoffMs(10), 30000);
  assert.equal(nextBackoffMs(1_000_000), 30000);
});

test('nextBackoffMs treats negative / non-integer / undefined as attempt 0', () => {
  // Defensive — caller bugs (e.g. NaN from `++` on undefined) should not turn
  // into Infinity sleeps. Reset-to-1s is the failure-mode we want.
  assert.equal(nextBackoffMs(-1), 1000);
  assert.equal(nextBackoffMs(-100), 1000);
  assert.equal(nextBackoffMs(undefined), 1000);
  assert.equal(nextBackoffMs(null), 1000);
  assert.equal(nextBackoffMs(NaN), 1000);
  assert.equal(nextBackoffMs(1.5), 1000);
  assert.equal(nextBackoffMs('2'), 1000);
});

// ─── isSubstantiveEvent ──────────────────────────────────────────────────────
// Issue #299 Codex High: the router emits synthetic `{"type":"connected"}`
// on every successful /inbox attach. If the backoff reset triggered on any
// parsed event, a 200 + connected + EOF cycle would reset reconnectAttempt
// every iteration and hot-loop. Only message/verdict/command count.

test('SUBSTANTIVE_EVENT_TYPES exact set', () => {
  assert.deepEqual([...SUBSTANTIVE_EVENT_TYPES].sort(), ['command', 'message', 'verdict']);
});

test('isSubstantiveEvent accepts message/verdict/command', () => {
  assert.equal(isSubstantiveEvent({ type: 'message', content: 'hi' }), true);
  assert.equal(isSubstantiveEvent({ type: 'verdict', verdict: 'allow' }), true);
  assert.equal(isSubstantiveEvent({ type: 'command', cmd: 'cancel' }), true);
});

test('isSubstantiveEvent rejects synthetic connected event', () => {
  // The exact payload the router sends on /inbox attach (emitted from
  // mercury-channel-router/router.cjs `/inbox/:id` handler via
  // `res.write('data: {"type":"connected"}\n\n')`). If this assertion
  // regresses, a 200 + connected + EOF server will hot-loop the reconnect
  // again.
  assert.equal(isSubstantiveEvent({ type: 'connected' }), false);
});

test('isSubstantiveEvent rejects unknown / missing / falsy types', () => {
  assert.equal(isSubstantiveEvent({ type: 'unknown' }), false);
  assert.equal(isSubstantiveEvent({}), false);
  assert.equal(isSubstantiveEvent({ type: '' }), false);
  assert.equal(isSubstantiveEvent({ type: null }), false);
  assert.equal(isSubstantiveEvent({ type: undefined }), false);
  assert.equal(isSubstantiveEvent(null), false);
  assert.equal(isSubstantiveEvent(undefined), false);
  assert.equal(isSubstantiveEvent('message'), false); // string, not object
  assert.equal(isSubstantiveEvent(42), false);
});
