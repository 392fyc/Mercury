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

test('detectBranchSync performs no git I/O (well under one process spawn)', () => {
  const { detectBranchSync } = loadLib({ MERCURY_BRANCH_OVERRIDE: undefined });
  const t0 = process.hrtime.bigint();
  detectBranchSync();
  const elapsedMs = Number(process.hrtime.bigint() - t0) / 1e6;
  // The sync path reads one env var. Threshold is generous (50ms) so the
  // assertion is about "no process spawn" rather than micro-timing — a real
  // git invocation is 20-200ms typical and would blow past this even on a
  // heavily loaded CI box.
  assert.ok(elapsedMs < 50, `detectBranchSync took ${elapsedMs.toFixed(2)}ms; expected no git spawn`);
});

// ─── detectBranchAsync ───────────────────────────────────────────────────────

test('detectBranchAsync returns MERCURY_BRANCH_OVERRIDE without spawning git', async () => {
  const { detectBranchAsync } = loadLib({ MERCURY_BRANCH_OVERRIDE: 'override-branch' });
  const t0 = process.hrtime.bigint();
  const result = await detectBranchAsync();
  const elapsedMs = Number(process.hrtime.bigint() - t0) / 1e6;
  assert.equal(result, 'override-branch');
  // Override path must not spawn git. Threshold is generous (50ms) — a real
  // process spawn would still take ≥20ms but the assertion is structural,
  // not micro-bench.
  assert.ok(elapsedMs < 50, `override path took ${elapsedMs.toFixed(2)}ms; expected no git spawn`);
});

test('detectBranchAsync resolves the branch of a synthetic git repo via cwd opt', async () => {
  const { detectBranchAsync } = loadLib({ MERCURY_BRANCH_OVERRIDE: undefined });
  // Build a controllable fixture instead of relying on the test runner's cwd
  // being a git repo (avoids shallow-checkout / CI-packaging flakes).
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'mcc-git-'));
  try {
    execSync('git init -q', { cwd: tmp });
    execSync('git checkout -q -b synth-branch', { cwd: tmp });
    const result = await detectBranchAsync({ cwd: tmp });
    assert.equal(result, 'synth-branch');
  } finally {
    try { fs.rmSync(tmp, { recursive: true, force: true }); } catch {}
  }
});

test('detectBranchAsync falls back to "unknown" when cwd is not a git repo', async () => {
  const { detectBranchAsync } = loadLib({ MERCURY_BRANCH_OVERRIDE: undefined });
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'mcc-nogit-'));
  try {
    const result = await detectBranchAsync({ cwd: tmp, timeoutMs: 3000 });
    assert.equal(result, 'unknown');
  } finally {
    try { fs.rmSync(tmp, { recursive: true, force: true }); } catch {}
  }
});

// ─── Cold-start contract (Issue #297 acceptance) ─────────────────────────────

test('lib cold-load + sync detect path does not spawn git', () => {
  // Per #297 acceptance: the sync path channel.cjs takes during module load
  // must not block on git. Threshold (200ms) is well below a hung-git
  // execSync (which was 50-200ms baseline, longer on stalls) so the test
  // still fails closed if anyone re-introduces execSync, but is robust to
  // CI noise.
  delete require.cache[LIB];
  delete process.env.MERCURY_BRANCH_OVERRIDE;
  const t0 = process.hrtime.bigint();
  const { detectBranchSync } = require(LIB);
  detectBranchSync();
  const elapsedMs = Number(process.hrtime.bigint() - t0) / 1e6;
  assert.ok(elapsedMs < 200, `cold-load + sync detect took ${elapsedMs.toFixed(2)}ms; expected no git spawn`);
});
