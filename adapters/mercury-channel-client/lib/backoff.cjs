'use strict';

// SSE reconnect exponential backoff for mercury-channel-client.
// Issue #299: connectInbox() previously slept a fixed 2s between reconnect
// attempts. With a wedged router and 3 concurrent sessions that's 1.5 req/s
// indefinitely. The sequence below is the smallest schedule that gives the
// router time to recover under load while still reconnecting promptly after
// a transient blip.
//
// Sequence: 1s, 2s, 5s, 10s, then 30s (clamped). Callers increment the
// attempt counter after each failed reconnect and reset to 0 once the
// connection has produced a substantive event (see isSubstantiveEvent) —
// proof the server is healthy, not just accepting and immediately closing.
const SCHEDULE_MS = [1000, 2000, 5000, 10000, 30000];

function nextBackoffMs(attempt) {
  const n = Number.isInteger(attempt) && attempt >= 0 ? attempt : 0;
  return SCHEDULE_MS[Math.min(n, SCHEDULE_MS.length - 1)];
}

// The router emits a synthetic `{"type":"connected"}` event immediately on
// every successful /inbox attach (mercury-channel-router/router.cjs:274), so
// "first parsed event" alone would reset the backoff on every successful
// 200 + connected + EOF cycle and turn the reconnect into a hot loop. Only
// types the consumer actually acts on count as proof of a healthy server.
const SUBSTANTIVE_EVENT_TYPES = new Set(['message', 'verdict', 'command']);
function isSubstantiveEvent(evt) {
  return !!evt && typeof evt === 'object' && SUBSTANTIVE_EVENT_TYPES.has(evt.type);
}

module.exports = { nextBackoffMs, SCHEDULE_MS, isSubstantiveEvent, SUBSTANTIVE_EVENT_TYPES };
