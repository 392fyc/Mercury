'use strict';

// Issue #304 regression — structural invariants for channel.cjs housekeeping.
// See ../mercury-channel-router/test/housekeeping-304.test.cjs for the
// rationale on source-text vs behavioral testing for these nits.

const test   = require('node:test');
const assert = require('node:assert/strict');
const fs     = require('node:fs');
const path   = require('node:path');

const clientSrc = fs.readFileSync(path.join(__dirname, '..', 'channel.cjs'), 'utf8');

test('#304 nit 1: channel.cjs magic numbers extracted to named constants', () => {
  assert.match(clientSrc, /const\s+ROUTER_HEALTH_PROBE_TIMEOUT_MS\s*=\s*500/);
  assert.match(clientSrc, /const\s+ROUTER_START_RETRY_ATTEMPTS\s*=\s*20/);
  assert.match(clientSrc, /const\s+ROUTER_START_RETRY_INTERVAL_MS\s*=\s*250/);
  assert.match(clientSrc, /const\s+READ_TOKEN_BOOT_RETRIES\s*=\s*10/);
  assert.match(clientSrc, /const\s+READ_TOKEN_BOOT_DELAY_MS\s*=\s*300/);
  // Every former literal must be replaced by the named constant — cover
  // the full set symmetrically so a partial regression is caught.
  assert.doesNotMatch(clientSrc, /AbortSignal\.timeout\(\s*500\s*\)/); // ROUTER_HEALTH_PROBE_TIMEOUT_MS
  assert.doesNotMatch(clientSrc, /for\s*\(\s*let\s+i\s*=\s*0\s*;\s*i\s*<\s*20\s*;/); // ROUTER_START_RETRY_ATTEMPTS
  assert.doesNotMatch(clientSrc, /setTimeout\(\s*r\s*=>\s*setTimeout[^,]*,\s*250\s*\)/); // ROUTER_START_RETRY_INTERVAL_MS (legacy nested form)
  assert.doesNotMatch(clientSrc, /new Promise\(r\s*=>\s*setTimeout\(\s*r\s*,\s*250\s*\)\)/);
  assert.doesNotMatch(clientSrc, /readToken\(\s*10\s*,\s*300\s*\)/);  // READ_TOKEN_BOOT_RETRIES / _DELAY_MS
});

test('#304 nit 3: routerFetch parameter renamed path_ → endpoint', () => {
  assert.match(clientSrc, /async\s+function\s+routerFetch\s*\(\s*endpoint\b/);
  // The shadowing-prone name must not be re-introduced.
  assert.doesNotMatch(clientSrc, /async\s+function\s+routerFetch\s*\(\s*path_\b/);
});
