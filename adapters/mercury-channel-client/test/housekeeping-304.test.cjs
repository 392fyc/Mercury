'use strict';

// Issue #304 regression — structural invariants for channel.cjs housekeeping.
// See ../mercury-channel-router/test/housekeeping-304.test.cjs for the
// rationale on source-text vs behavioral testing for these nits.
//
// #303 split: channel.cjs is now wiring only; constants and helpers moved
// to lib/{state,router-bootstrap,mcp-tools,sse}.cjs. Each constant is
// asserted in its owning submodule so a future re-extraction does not
// silently rehome the named constants under a different name.

const test   = require('node:test');
const assert = require('node:assert/strict');
const fs     = require('node:fs');
const path   = require('node:path');

const read = rel => fs.readFileSync(path.join(__dirname, '..', rel), 'utf8');

const channelSrc   = read('channel.cjs');
const bootstrapSrc = read('lib/router-bootstrap.cjs');
const mcpSrc       = read('lib/mcp-tools.cjs');
const sseSrc       = read('lib/sse.cjs');
const stateSrc     = read('lib/state.cjs');

test('#304 nit 1: magic numbers extracted to named constants in owning submodules', () => {
  // ROUTER_* live with ensureRouter() in router-bootstrap.cjs.
  assert.match(bootstrapSrc, /const\s+ROUTER_HEALTH_PROBE_TIMEOUT_MS\s*=\s*500/);
  assert.match(bootstrapSrc, /const\s+ROUTER_START_RETRY_ATTEMPTS\s*=\s*20/);
  assert.match(bootstrapSrc, /const\s+ROUTER_START_RETRY_INTERVAL_MS\s*=\s*250/);
  // READ_TOKEN_BOOT_* stay with the initial readToken call site in channel.cjs.
  assert.match(channelSrc, /const\s+READ_TOKEN_BOOT_RETRIES\s*=\s*10/);
  assert.match(channelSrc, /const\s+READ_TOKEN_BOOT_DELAY_MS\s*=\s*300/);
  // Every former literal must be replaced by the named constant across the
  // whole package. Cover the full set symmetrically so a partial regression
  // is caught.
  const allSrc = channelSrc + bootstrapSrc + mcpSrc + sseSrc + stateSrc;
  assert.doesNotMatch(allSrc, /AbortSignal\.timeout\(\s*500\s*\)/); // ROUTER_HEALTH_PROBE_TIMEOUT_MS
  assert.doesNotMatch(allSrc, /for\s*\(\s*let\s+i\s*=\s*0\s*;\s*i\s*<\s*20\s*;/); // ROUTER_START_RETRY_ATTEMPTS
  assert.doesNotMatch(allSrc, /new Promise\(r\s*=>\s*setTimeout\(\s*r\s*,\s*250\s*\)\)/); // ROUTER_START_RETRY_INTERVAL_MS
  assert.doesNotMatch(allSrc, /readToken\(\s*10\s*,\s*300\s*\)/);   // READ_TOKEN_BOOT_RETRIES / _DELAY_MS
});

test('#304 nit 3: routerFetch parameter renamed path_ → endpoint', () => {
  // After #303 split, routerFetch lives in router-bootstrap.cjs.
  assert.match(bootstrapSrc, /async\s+function\s+routerFetch\s*\(\s*endpoint\b/);
  // The shadowing-prone name must not be re-introduced anywhere.
  const allSrc = channelSrc + bootstrapSrc + mcpSrc + sseSrc + stateSrc;
  assert.doesNotMatch(allSrc, /async\s+function\s+routerFetch\s*\(\s*path_\b/);
});

test('#303 split: channel.cjs is wiring only and all submodules ≤200 LOC', () => {
  // Issue #303 acceptance: each submodule ≤200 LOC after the architectural
  // split. A regression that re-monolithizes channel.cjs would push it back
  // over the cap and reintroduce the legibility / review-iteration problems
  // PR #295 surfaced.
  const lineCount = src => src.split('\n').length;
  const limits = {
    'channel.cjs':             lineCount(channelSrc),
    'lib/state.cjs':           lineCount(stateSrc),
    'lib/router-bootstrap.cjs': lineCount(bootstrapSrc),
    'lib/mcp-tools.cjs':       lineCount(mcpSrc),
    'lib/sse.cjs':             lineCount(sseSrc),
  };
  for (const [name, lines] of Object.entries(limits)) {
    assert.ok(lines <= 200, `${name} is ${lines} LOC; #303 acceptance caps each submodule at ≤200`);
  }
});
