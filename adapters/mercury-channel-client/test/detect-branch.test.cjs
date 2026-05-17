'use strict';
// detect-branch.test.cjs — Unit tests for lib/detect-branch.cjs (Issue #297).
// Uses node:test (built-in, no external deps).
//
// Run: node --test adapters/mercury-channel-client/test/detect-branch.test.cjs

const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { execSync } = require('child_process');

const LIB = path.resolve(__dirname, '..', 'lib', 'detect-branch.cjs');

// Reload the lib with a clean env so MERCURY_BRANCH_OVERRIDE behaves predictably.
function loadLib(envOverrides = {}) {
  for (const k of Object.keys(envOverrides)) {
    if (envOverrides[k] === undefined) delete process.env[k];
    else process.env[k] = envOverrides[k];
  }
  delete require.cache[LIB];
  return require(LIB);
}

// ─── detectBranchSync ────────────────────────────────────────────────────────

test('detectBranchSync returns MERCURY_BRANCH_OVERRIDE when set', () => {
  const { detectBranchSync } = loadLib({ MERCURY_BRANCH_OVERRIDE: 'my-branch' });
  assert.equal(detectBranchSync(), 'my-branch');
});

test('detectBranchSync returns "unknown" when override unset', () => {
  const { detectBranchSync } = loadLib({ MERCURY_BRANCH_OVERRIDE: undefined });
  assert.equal(detectBranchSync(), 'unknown');
});

test('detectBranchSync performs no I/O (under 5ms)', () => {
  const { detectBranchSync } = loadLib({ MERCURY_BRANCH_OVERRIDE: undefined });
  const t0 = process.hrtime.bigint();
  detectBranchSync();
  const elapsedMs = Number(process.hrtime.bigint() - t0) / 1e6;
  assert.ok(elapsedMs < 5, `detectBranchSync took ${elapsedMs.toFixed(2)}ms, expected <5ms`);
});

// ─── detectBranchAsync ───────────────────────────────────────────────────────

test('detectBranchAsync returns MERCURY_BRANCH_OVERRIDE without execFile', async () => {
  const { detectBranchAsync } = loadLib({ MERCURY_BRANCH_OVERRIDE: 'override-branch' });
  const t0 = process.hrtime.bigint();
  const result = await detectBranchAsync();
  const elapsedMs = Number(process.hrtime.bigint() - t0) / 1e6;
  assert.equal(result, 'override-branch');
  // Override path must not spawn git; well under a process spawn (~20ms typical).
  assert.ok(elapsedMs < 10, `override path took ${elapsedMs.toFixed(2)}ms, expected <10ms`);
});

test('detectBranchAsync resolves current git branch in a repo cwd', async () => {
  const { detectBranchAsync } = loadLib({ MERCURY_BRANCH_OVERRIDE: undefined });
  const result = await detectBranchAsync();
  // We are running inside the Mercury repo on a real branch; result should be
  // non-empty and not contain whitespace.
  assert.ok(typeof result === 'string' && result.length > 0, `unexpected: ${JSON.stringify(result)}`);
  assert.doesNotMatch(result, /\s/, `branch should not contain whitespace: ${result}`);
  // Cross-check against git directly (sanity).
  const expected = execSync('git branch --show-current', { encoding: 'utf8' }).trim() || 'unknown';
  assert.equal(result, expected);
});

test('detectBranchAsync falls back to "unknown" outside a git repo', async () => {
  const { detectBranchAsync } = loadLib({ MERCURY_BRANCH_OVERRIDE: undefined });
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'mcc-test-'));
  const savedCwd = process.cwd();
  try {
    process.chdir(tmp);
    const result = await detectBranchAsync({ timeoutMs: 3000 });
    assert.equal(result, 'unknown');
  } finally {
    process.chdir(savedCwd);
    try { fs.rmdirSync(tmp); } catch {}
  }
});

// ─── Cold-start contract (Issue #297 acceptance) ─────────────────────────────

test('lib module load + detectBranchSync stays under 50ms', () => {
  // Simulate cold start: fresh require + sync call. This is the path channel.cjs
  // takes during module evaluation; per #297 acceptance it must not block.
  delete require.cache[LIB];
  delete process.env.MERCURY_BRANCH_OVERRIDE;
  const t0 = process.hrtime.bigint();
  const { detectBranchSync } = require(LIB);
  detectBranchSync();
  const elapsedMs = Number(process.hrtime.bigint() - t0) / 1e6;
  assert.ok(elapsedMs < 50, `cold-load + sync detect took ${elapsedMs.toFixed(2)}ms, expected <50ms`);
});
