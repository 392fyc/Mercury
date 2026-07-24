#!/usr/bin/env node
'use strict';

// Mercury Loop Detector — Test suite
// Runner: node --test adapters/mercury-loop-detector/*.test.cjs

const { test, describe, before, after, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fs     = require('fs');
const path   = require('path');
const os     = require('os');
const crypto = require('crypto');

const { checkMultiLevel, updateTimestamps, resolveThresholds, TIMEOUT_DEFAULTS } = require('./timeout.cjs');
const { writeStallReport, pruneReports, isoFsSafe } = require('./report.cjs');

// ── Test helpers ─────────────────────────────────────────────────────────────

function makeTmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'mercury-ld-test-'));
}

function makeState(overrides = {}) {
  return {
    session_id: 'test-session-001',
    dup_count: 0, dup_tool: null, dup_hash: null,
    err_count: 0, err_last: null,
    read_count: 0, np_count: 0,
    last_activity_ts: null, last_write_ts: null, last_progress_ts: null,
    ...overrides
  };
}

function makeCfg(overrides = {}) {
  return {
    enabled: true,
    no_progress_threshold: 5,
    same_error_threshold: 5,
    duplicate_call_threshold: 3,
    read_write_ratio_threshold: 12,  // matches DEFAULTS in hook.cjs (Issue #306)
    read_write_ratio_disabled: false,
    ...overrides
  };
}

function captureStderr(fn) {
  const chunks = [];
  const orig = process.stderr.write.bind(process.stderr);
  process.stderr.write = (chunk, ...args) => { chunks.push(String(chunk)); return true; };
  try { fn(); } finally { process.stderr.write = orig; }
  return chunks.join('');
}

// ── 1. Existing 4 signals — regression sanity ────────────────────────────────

describe('existing signals (regression)', () => {
  test('duplicate_call fires at threshold', () => {
    const state = makeState({ dup_count: 3, dup_tool: 'Bash', dup_hash: 'abcd1234' });
    const cfg   = makeCfg({ duplicate_call_threshold: 3 });
    const stall = detectStall(state, cfg);
    assert.equal(stall.type, 'duplicate_call');
    assert.match(stall.reason, /duplicate_call/);
  });

  test('same_error fires at threshold', () => {
    const state = makeState({ err_count: 5, err_last: 'error: file not found' });
    const cfg   = makeCfg({ same_error_threshold: 5 });
    const stall = detectStall(state, cfg);
    assert.equal(stall.type, 'same_error');
    assert.match(stall.reason, /same_error/);
  });

  test('read_write_ratio fires at threshold', () => {
    const state = makeState({ read_count: 8 });
    const cfg   = makeCfg({ read_write_ratio_threshold: 8 });
    const stall = detectStall(state, cfg);
    assert.equal(stall.type, 'read_write_ratio');
    assert.match(stall.reason, /read_write_ratio/);
  });

  test('no_progress fires at threshold', () => {
    const state = makeState({ np_count: 5 });
    const cfg   = makeCfg({ no_progress_threshold: 5 });
    const stall = detectStall(state, cfg);
    assert.equal(stall.type, 'no_progress');
    assert.match(stall.reason, /no_progress/);
  });

  test('no stall below threshold', () => {
    const state = makeState({ dup_count: 2, err_count: 4, read_count: 7, np_count: 4 });
    const stall = detectStall(state, makeCfg());
    assert.equal(stall, null);
  });

  test('read_write_ratio gated by cfg.read_write_ratio_disabled flag (#306 mirror sync)', () => {
    // Inline mirror MUST honor the flag in lockstep with production hook.cjs.
    // If a future change drops the gate from the mirror, this test fails.
    const state = makeState({ read_count: 99 });
    const cfgEnabled  = makeCfg({ read_write_ratio_disabled: false });
    const cfgDisabled = makeCfg({ read_write_ratio_disabled: true });
    assert.equal(detectStall(state, cfgEnabled).type, 'read_write_ratio',
      'flag=false: read-ratio fires at read_count=99 vs threshold=12');
    assert.equal(detectStall(state, cfgDisabled), null,
      'flag=true: read-ratio gate blocks the heuristic regardless of read_count');
  });
});

// Inline detectStall mirror — kept in sync with hook.cjs's detectStall().
// Tests requiring research-mode behavior must set cfg.read_write_ratio_disabled=true
// (handled by makeCfg() default `false`).
function detectStall(state, cfg) {
  if (state.dup_count  >= cfg.duplicate_call_threshold)
    return { type: 'duplicate_call',   reason: `duplicate_call: ${state.dup_count} identical ${state.dup_tool} calls (hash:${state.dup_hash})` };
  if (state.err_count  >= cfg.same_error_threshold)
    return { type: 'same_error',       reason: `same_error: ${state.err_count} identical errors — "${state.err_last}"` };
  if (!cfg.read_write_ratio_disabled && state.read_count >= cfg.read_write_ratio_threshold)
    return { type: 'read_write_ratio', reason: `read_write_ratio: ${state.read_count} consecutive read-only calls with no writes` };
  if (state.np_count   >= cfg.no_progress_threshold)
    return { type: 'no_progress',      reason: `no_progress: ${state.np_count} consecutive action calls with no file write` };
  return null;
}

// ── 2. Soft/Idle timeout — warn to stderr, do not block ─────────────────────

describe('timeout: soft and idle warn, do not block', () => {
  test('soft timeout returns level=soft, should_block=false', () => {
    const now   = Date.now();
    const state = makeState({ last_write_ts: now - 70_000 }); // 70s ago
    const cfg   = makeCfg({ timeout_soft_sec: 60, timeout_idle_sec: 300, timeout_hard_sec: 900 });
    const result = checkMultiLevel(state, cfg, now);
    assert.ok(result, 'should return non-null');
    assert.equal(result.level, 'soft');
    assert.equal(result.should_block, false);
    assert.match(result.message, /soft timeout/);
  });

  test('idle timeout returns level=idle, should_block=false', () => {
    const now   = Date.now();
    const state = makeState({ last_write_ts: now - 310_000 }); // 310s ago
    const cfg   = makeCfg({ timeout_soft_sec: 60, timeout_idle_sec: 300, timeout_hard_sec: 900 });
    const result = checkMultiLevel(state, cfg, now);
    assert.ok(result);
    assert.equal(result.level, 'idle');
    assert.equal(result.should_block, false);
    assert.match(result.message, /idle timeout/);
    assert.match(result.message, /handoff/);
  });

  test('no timeout when within soft threshold', () => {
    const now   = Date.now();
    const state = makeState({ last_write_ts: now - 30_000 }); // 30s ago
    const cfg   = makeCfg({ timeout_soft_sec: 60 });
    const result = checkMultiLevel(state, cfg, now);
    assert.equal(result, null);
  });

  test('no timeout when last_write_ts is null (first call)', () => {
    const now   = Date.now();
    const state = makeState({ last_write_ts: null });
    const cfg   = makeCfg();
    // updateTimestamps initialises last_write_ts to now on first call
    updateTimestamps(state, false, false, now);
    const result = checkMultiLevel(state, cfg, now);
    assert.equal(result, null);
  });
});

// ── 3. Hard timeout — block + write diagnostic report ────────────────────────

describe('timeout: hard blocks and writes report', () => {
  let tmpDir;
  beforeEach(() => { tmpDir = makeTmpDir(); });
  afterEach(() => { fs.rmSync(tmpDir, { recursive: true, force: true }); });

  test('hard timeout returns level=hard, should_block=true', () => {
    const now   = Date.now();
    const state = makeState({ last_write_ts: now - 910_000 }); // 910s ago
    const cfg   = makeCfg({ timeout_soft_sec: 60, timeout_idle_sec: 300, timeout_hard_sec: 900 });
    const result = checkMultiLevel(state, cfg, now);
    assert.ok(result);
    assert.equal(result.level, 'hard');
    assert.equal(result.should_block, true);
  });

  test('hard timeout + writeStallReport creates report file', () => {
    const state = makeState({ last_write_ts: Date.now() - 910_000 });
    const last_tool = { name: 'Bash', input_hash: 'abcd1234', errored: false, err_sig: null };
    const fpath = writeStallReport(tmpDir, 'sess-001', 'timeout_hard', 'hard timeout reason', state, last_tool);
    assert.ok(fpath, 'should return file path');
    assert.ok(fs.existsSync(fpath), 'file should exist');
    const report = JSON.parse(fs.readFileSync(fpath, 'utf8'));
    assert.equal(report.stall_type, 'timeout_hard');
    assert.equal(report.session_id, 'sess-001');
    assert.equal(report.last_tool.name, 'Bash');
    assert.equal(typeof report.timestamp, 'string');
  });
});

// ── 4. detectStall non-null → write diagnostic report ───────────────────────

describe('detectStall fires → writeStallReport', () => {
  let tmpDir;
  beforeEach(() => { tmpDir = makeTmpDir(); });
  afterEach(() => { fs.rmSync(tmpDir, { recursive: true, force: true }); });

  test('stall report is written on detectStall hit', () => {
    const state = makeState({ dup_count: 3, dup_tool: 'Read', dup_hash: 'aabb1122' });
    const last_tool = { name: 'Read', input_hash: 'aabb1122', errored: false, err_sig: null };
    const stall = detectStall(state, makeCfg({ duplicate_call_threshold: 3 }));
    assert.ok(stall);
    const fpath = writeStallReport(tmpDir, 'sess-dup-001', stall.type, stall.reason, state, last_tool);
    assert.ok(fpath);
    const report = JSON.parse(fs.readFileSync(fpath, 'utf8'));
    assert.equal(report.stall_type, 'duplicate_call');
    assert.equal(report.state_snapshot.dup_count, 3);
  });

  test('report contains state_snapshot with all required fields', () => {
    const state = makeState({
      dup_count: 2, dup_tool: 'Glob', dup_hash: 'ccdd3344',
      err_count: 1, err_last: 'error: test',
      read_count: 5, np_count: 3,
      last_activity_ts: 1000, last_write_ts: 900
    });
    const fpath = writeStallReport(tmpDir, 'sess-snap', 'no_progress', 'reason', state,
      { name: 'Bash', input_hash: 'aabb', errored: true, err_sig: 'error: test' });
    const report = JSON.parse(fs.readFileSync(fpath, 'utf8'));
    const snap = report.state_snapshot;
    assert.equal(snap.dup_count, 2);
    assert.equal(snap.dup_tool, 'Glob');
    assert.equal(snap.err_count, 1);
    assert.equal(snap.read_count, 5);
    assert.equal(snap.np_count, 3);
    assert.equal(snap.last_activity_ts, 1000);
    assert.equal(snap.last_write_ts, 900);
    assert.equal(report.last_tool.errored, true);
    assert.equal(report.last_tool.err_sig, 'error: test');
  });
});

// ── 5. Pruning keeps ≤50 files ───────────────────────────────────────────────

describe('pruneReports keeps newest 50', () => {
  let tmpDir;
  before(() => {
    tmpDir = makeTmpDir();
    const dir = path.join(tmpDir, '.mercury', 'state', 'stall-reports');
    fs.mkdirSync(dir, { recursive: true });
    // Create 60 files with distinct mtimes
    for (let i = 0; i < 60; i++) {
      const fp = path.join(dir, `sess-${String(i).padStart(3,'0')}-2026-04-24T${String(i).padStart(6,'0')}Z.json`);
      fs.writeFileSync(fp, JSON.stringify({ i }));
      // Stagger mtime by setting atime+mtime via utimes
      const t = (Date.now() / 1000) + i;
      fs.utimesSync(fp, t, t);
    }
  });
  after(() => { fs.rmSync(tmpDir, { recursive: true, force: true }); });

  test('prune leaves exactly 50 files', () => {
    pruneReports(tmpDir, 50);
    const dir   = path.join(tmpDir, '.mercury', 'state', 'stall-reports');
    const files = fs.readdirSync(dir).filter(f => f.endsWith('.json'));
    assert.equal(files.length, 50);
  });

  test('prune keeps the newest files', () => {
    const dir   = path.join(tmpDir, '.mercury', 'state', 'stall-reports');
    const files = fs.readdirSync(dir)
      .filter(f => f.endsWith('.json'))
      .map(f => ({ f, mtime: fs.statSync(path.join(dir, f)).mtimeMs }))
      .sort((a, b) => b.mtime - a.mtime);
    // Oldest remaining should be newer than deleted ones (i >= 10)
    const oldest = files[files.length - 1];
    // Files were named sess-000 through sess-059; we kept 50 newest (sess-010 to sess-059)
    assert.match(oldest.f, /sess-0[1-5]/);
  });
});

// ── 6. MERCURY_STALL_REPORT_DISABLED skips write ─────────────────────────────

describe('MERCURY_STALL_REPORT_DISABLED feature flag', () => {
  let tmpDir;
  before(() => { tmpDir = makeTmpDir(); });
  after(() => { fs.rmSync(tmpDir, { recursive: true, force: true }); delete process.env.MERCURY_STALL_REPORT_DISABLED; });

  test('disabled=1 returns null and writes nothing', () => {
    process.env.MERCURY_STALL_REPORT_DISABLED = '1';
    const state = makeState();
    const result = writeStallReport(tmpDir, 'sess-disabled', 'no_progress', 'reason', state,
      { name: 'Bash', input_hash: 'aa', errored: false, err_sig: null });
    assert.equal(result, null);
    const dir = path.join(tmpDir, '.mercury', 'state', 'stall-reports');
    const exists = fs.existsSync(dir) && fs.readdirSync(dir).length > 0;
    assert.equal(exists, false, 'no files should be written');
  });

  test('disabled=0 (other value) proceeds normally', () => {
    process.env.MERCURY_STALL_REPORT_DISABLED = '0';
    const state = makeState();
    const fpath = writeStallReport(tmpDir, 'sess-enabled', 'no_progress', 'reason', state,
      { name: 'Bash', input_hash: 'bb', errored: false, err_sig: null });
    assert.ok(fpath, 'should write report when not disabled');
    delete process.env.MERCURY_STALL_REPORT_DISABLED;
  });
});

// ── 7. Config file threshold override ────────────────────────────────────────

describe('config file threshold override', () => {
  test('resolveThresholds uses config values when no env vars', () => {
    // Clear env vars
    const saved = {};
    for (const k of ['MERCURY_TIMEOUT_SOFT_SEC', 'MERCURY_TIMEOUT_IDLE_SEC', 'MERCURY_TIMEOUT_HARD_SEC']) {
      saved[k] = process.env[k]; delete process.env[k];
    }
    const cfg = makeCfg({ timeout_soft_sec: 120, timeout_idle_sec: 600, timeout_hard_sec: 1800 });
    const thresholds = resolveThresholds(cfg);
    assert.equal(thresholds.soft, 120);
    assert.equal(thresholds.idle, 600);
    assert.equal(thresholds.hard, 1800);
    for (const [k, v] of Object.entries(saved)) { if (v !== undefined) process.env[k] = v; }
  });

  test('env var overrides config file values', () => {
    process.env.MERCURY_TIMEOUT_SOFT_SEC  = '45';
    process.env.MERCURY_TIMEOUT_IDLE_SEC  = '200';
    process.env.MERCURY_TIMEOUT_HARD_SEC  = '800';
    const cfg = makeCfg({ timeout_soft_sec: 120, timeout_idle_sec: 600, timeout_hard_sec: 1800 });
    const thresholds = resolveThresholds(cfg);
    assert.equal(thresholds.soft, 45);
    assert.equal(thresholds.idle, 200);
    assert.equal(thresholds.hard, 800);
    delete process.env.MERCURY_TIMEOUT_SOFT_SEC;
    delete process.env.MERCURY_TIMEOUT_IDLE_SEC;
    delete process.env.MERCURY_TIMEOUT_HARD_SEC;
  });

  test('clamp rejects out-of-range values, falls back to default', () => {
    const saved = {};
    for (const k of ['MERCURY_TIMEOUT_SOFT_SEC', 'MERCURY_TIMEOUT_IDLE_SEC', 'MERCURY_TIMEOUT_HARD_SEC']) {
      saved[k] = process.env[k]; delete process.env[k];
    }
    // undefined config fields → resolveThresholds falls back to TIMEOUT_DEFAULTS
    const cfg = makeCfg({ timeout_soft_sec: undefined, timeout_idle_sec: 0, timeout_hard_sec: 99999 });
    const thresholds = resolveThresholds(cfg);
    assert.equal(thresholds.soft, TIMEOUT_DEFAULTS.timeout_soft_sec);  // fallback
    assert.equal(thresholds.idle, TIMEOUT_DEFAULTS.timeout_idle_sec);  // 0 out of range
    assert.equal(thresholds.hard, TIMEOUT_DEFAULTS.timeout_hard_sec);  // 99999 out of range
    for (const [k, v] of Object.entries(saved)) { if (v !== undefined) process.env[k] = v; }
  });
});

// ── 8. Invalid session_id — fail-open, no report written ────────────────────

describe('invalid session_id fails open', () => {
  let tmpDir;
  before(() => { tmpDir = makeTmpDir(); });
  after(() => { fs.rmSync(tmpDir, { recursive: true, force: true }); });

  test('empty string session_id returns null', () => {
    let warnMsg = '';
    const origWrite = process.stderr.write.bind(process.stderr);
    process.stderr.write = (chunk, ...a) => { warnMsg += String(chunk); return true; };
    const result = writeStallReport(tmpDir, '', 'no_progress', 'reason', makeState(),
      { name: 'Bash', input_hash: 'aa', errored: false, err_sig: null });
    process.stderr.write = origWrite;
    assert.equal(result, null);
    assert.match(warnMsg, /empty session_id/);
  });

  test('null session_id returns null', () => {
    const origWrite = process.stderr.write.bind(process.stderr);
    process.stderr.write = () => true;
    const result = writeStallReport(tmpDir, null, 'no_progress', 'reason', makeState(),
      { name: 'Bash', input_hash: 'aa', errored: false, err_sig: null });
    process.stderr.write = origWrite;
    assert.equal(result, null);
  });

  test('whitespace-only session_id returns null', () => {
    const origWrite = process.stderr.write.bind(process.stderr);
    process.stderr.write = () => true;
    const result = writeStallReport(tmpDir, '   ', 'no_progress', 'reason', makeState(),
      { name: 'Bash', input_hash: 'aa', errored: false, err_sig: null });
    process.stderr.write = origWrite;
    assert.equal(result, null);
  });
});

// ── 10. Security: session_id path traversal prevention ───────────────────────

describe('writeStallReport session_id sanitization', () => {
  let tmpDir;
  beforeEach(() => { tmpDir = makeTmpDir(); });
  afterEach(() => { fs.rmSync(tmpDir, { recursive: true, force: true }); });

  test('traversal session_id "../evil" writes safe file inside stall-reports/', () => {
    const state = makeState();
    const last_tool = { name: 'Bash', input_hash: 'aa', errored: false, err_sig: null };
    const fpath = writeStallReport(tmpDir, '../evil', 'no_progress', 'reason', state, last_tool);
    assert.ok(fpath, 'should return a file path');
    // File must be inside the expected stall-reports dir, not above it
    const reportDir = path.join(tmpDir, '.mercury', 'state', 'stall-reports');
    assert.ok(fpath.startsWith(reportDir), `fpath ${fpath} must start with ${reportDir}`);
    assert.ok(fs.existsSync(fpath), 'file must exist');
    // Filename must not contain slashes or ".." sequences
    const fname = path.basename(fpath);
    assert.ok(!fname.includes('/') && !fname.includes('\\'), 'no path separator in filename');
    assert.ok(!fname.includes('..'), 'no ".." in filename');
    // Report JSON preserves original session_id for traceability
    const report = JSON.parse(fs.readFileSync(fpath, 'utf8'));
    assert.equal(report.session_id, '../evil');
  });

  test('all-slash session_id "///" returns null + stderr warn', () => {
    const state = makeState();
    const last_tool = { name: 'Bash', input_hash: 'bb', errored: false, err_sig: null };
    let warnMsg = '';
    const origWrite = process.stderr.write.bind(process.stderr);
    process.stderr.write = (chunk, ...a) => { warnMsg += String(chunk); return true; };
    const result = writeStallReport(tmpDir, '///', 'no_progress', 'reason', state, last_tool);
    process.stderr.write = origWrite;
    assert.equal(result, null);
    assert.match(warnMsg, /no safe chars/);
  });

  // Optional: verify mode 0o600 on Unix (skipped on Windows)
  test('report file has mode 0o600 (Unix only)', { skip: process.platform === 'win32' }, () => {
    const state = makeState();
    const last_tool = { name: 'Bash', input_hash: 'cc', errored: false, err_sig: null };
    const fpath = writeStallReport(tmpDir, 'sess-mode-test', 'no_progress', 'reason', state, last_tool);
    assert.ok(fpath, 'should write file');
    const mode = fs.statSync(fpath).mode & 0o777;
    assert.equal(mode, 0o600, `expected mode 0o600, got 0o${mode.toString(8)}`);
  });
});

// ── 11. Security: resolveThresholds order validation ─────────────────────────

describe('resolveThresholds order validation', () => {
  test('reversed env vars (soft=900, idle=60, hard=30) → TIMEOUT_DEFAULTS + stderr warn', () => {
    process.env.MERCURY_TIMEOUT_SOFT_SEC = '900';
    process.env.MERCURY_TIMEOUT_IDLE_SEC = '60';
    process.env.MERCURY_TIMEOUT_HARD_SEC = '30';
    let warnMsg = '';
    const origWrite = process.stderr.write.bind(process.stderr);
    process.stderr.write = (chunk, ...a) => { warnMsg += String(chunk); return true; };
    const result = resolveThresholds(makeCfg());
    process.stderr.write = origWrite;
    delete process.env.MERCURY_TIMEOUT_SOFT_SEC;
    delete process.env.MERCURY_TIMEOUT_IDLE_SEC;
    delete process.env.MERCURY_TIMEOUT_HARD_SEC;
    assert.equal(result.soft, TIMEOUT_DEFAULTS.timeout_soft_sec);
    assert.equal(result.idle, TIMEOUT_DEFAULTS.timeout_idle_sec);
    assert.equal(result.hard, TIMEOUT_DEFAULTS.timeout_hard_sec);
    assert.match(warnMsg, /soft<=idle<=hard/);
  });
});

// ── isoFsSafe helper ──────────────────────────────────────────────────────────

describe('isoFsSafe formatting', () => {
  test('removes colons and dots, retains milliseconds', () => {
    const d = new Date('2026-04-24T12:34:56.789Z');
    const s = isoFsSafe(d);
    assert.equal(s, '2026-04-24T123456789Z');
    assert.ok(!s.includes(':'), 'no colons');
    assert.ok(!s.includes('.'), 'no dots');
    assert.ok(s.includes('789'), 'milliseconds retained');
  });
});

// ── 12. PROGRESS_TOOLS classification (Issue #325) ──────────────────────────

describe('update() progress signal classification', () => {
  // Pull the actual update fn from hook.cjs to keep behavior in sync — no mirror.
  const { update, PROGRESS_TOOLS } = require('./hook.cjs');

  function callUpdate(state, tool, opts = {}) {
    const { is_write = false, is_read = false, errored = false, err_sig = null, hash = 'h1' } = opts;
    const is_progress = PROGRESS_TOOLS.has(tool);
    update(state, tool, hash, is_write, is_read, is_progress, errored, err_sig);
  }

  test('PROGRESS_TOOLS set contains expected tools', () => {
    for (const t of ['Bash', 'Agent', 'Skill', 'ToolSearch',
                     'Task', 'TaskCreate', 'TaskUpdate', 'TaskList', 'TaskGet', 'TaskOutput', 'TaskStop']) {
      assert.ok(PROGRESS_TOOLS.has(t), `${t} should be in PROGRESS_TOOLS`);
    }
  });

  test('Bash with success resets np_count', () => {
    const state = makeState({ np_count: 4 });
    callUpdate(state, 'Bash');
    assert.equal(state.np_count, 0, 'Bash should reset np_count');
  });

  test('Agent call resets np_count', () => {
    const state = makeState({ np_count: 4 });
    callUpdate(state, 'Agent');
    assert.equal(state.np_count, 0);
  });

  test('All Task* variants reset np_count', () => {
    for (const t of ['Task', 'TaskCreate', 'TaskUpdate', 'TaskList', 'TaskGet', 'TaskOutput', 'TaskStop']) {
      const state = makeState({ np_count: 4 });
      callUpdate(state, t);
      assert.equal(state.np_count, 0, `${t} should reset np_count`);
    }
  });

  test('Skill resets np_count', () => {
    const state = makeState({ np_count: 4 });
    callUpdate(state, 'Skill');
    assert.equal(state.np_count, 0);
  });

  test('ToolSearch resets np_count', () => {
    const state = makeState({ np_count: 4 });
    callUpdate(state, 'ToolSearch');
    assert.equal(state.np_count, 0);
  });

  test('WebSearch still increments np_count (true-stall pattern preserved)', () => {
    const state = makeState({ np_count: 4 });
    callUpdate(state, 'WebSearch');
    assert.equal(state.np_count, 5, 'WebSearch should not be in PROGRESS_TOOLS');
  });

  test('WebFetch still increments np_count', () => {
    const state = makeState({ np_count: 4 });
    callUpdate(state, 'WebFetch');
    assert.equal(state.np_count, 5);
  });

  test('mcp__foo still increments np_count', () => {
    const state = makeState({ np_count: 4 });
    callUpdate(state, 'mcp__claude_ai_Gmail__authenticate');
    assert.equal(state.np_count, 5, 'MCP tools default to non-progress');
  });

  test('5 consecutive Bash calls do NOT trigger no_progress (#325 fix)', () => {
    const state = makeState();
    for (let i = 0; i < 5; i++) {
      callUpdate(state, 'Bash', { hash: `h${i}` });
    }
    assert.equal(state.np_count, 0, 'np_count should never accumulate for Bash');
    const stall = detectStall(state, makeCfg());
    assert.equal(stall, null, 'no stall expected from Bash burst');
  });

  test('5 consecutive WebSearch calls DO trigger no_progress (true-stall preserved)', () => {
    const state = makeState();
    for (let i = 0; i < 5; i++) {
      callUpdate(state, 'WebSearch', { hash: `h${i}` });
    }
    assert.equal(state.np_count, 5);
    const stall = detectStall(state, makeCfg());
    assert.ok(stall, 'WebSearch burst should trigger stall');
    assert.equal(stall.type, 'no_progress');
  });

  test('Bash with error still resets np_count via errored branch', () => {
    const state = makeState({ np_count: 4 });
    callUpdate(state, 'Bash', { errored: true, err_sig: 'error: fail' });
    assert.equal(state.np_count, 0, 'errored=true also resets np_count');
  });

  test('Mixed burst: 3 Bash + 2 WebSearch keeps np_count at 2 (only WebSearch counted)', () => {
    const state = makeState();
    for (let i = 0; i < 3; i++) callUpdate(state, 'Bash',      { hash: `b${i}` });
    for (let i = 0; i < 2; i++) callUpdate(state, 'WebSearch', { hash: `w${i}` });
    assert.equal(state.np_count, 2);
  });
});

// ── 13. hook.cjs ETE: PROGRESS_TOOLS via process spawn ──────────────────────

describe('hook.cjs ETE: PROGRESS_TOOLS prevents false positive', () => {
  const { execFileSync } = require('child_process');
  const HOOK = path.join(__dirname, 'hook.cjs');
  let counter = 0;
  let tmpDirs = [];

  function freshEnv(tmpDir) { return { ...process.env, CLAUDE_PROJECT_DIR: tmpDir }; }
  function uniqueSession()  { return `ete-prog-${process.pid}-${++counter}-${Date.now()}`; }

  afterEach(() => {
    for (const d of tmpDirs) {
      try { fs.rmSync(d, { recursive: true, force: true }); } catch { /* ignore */ }
    }
    tmpDirs = [];
  });

  test('ETE: pre-seeded np_count=4 + Bash call does NOT block (was previous false positive)', () => {
    const tmpDir = makeTmpDir();
    tmpDirs.push(tmpDir);
    const session_id = uniqueSession();
    const stateDir = path.join(tmpDir, '.mercury', 'state');
    fs.mkdirSync(stateDir, { recursive: true });
    fs.writeFileSync(path.join(stateDir, 'loop-detector.json'), JSON.stringify({
      session_id,
      dup_count: 0, dup_tool: null, dup_hash: null,
      err_count: 0, err_last: null,
      read_count: 0, np_count: 4,
      last_activity_ts: Date.now(), last_write_ts: Date.now()
    }, null, 2));

    let stdout = '';
    try {
      stdout = execFileSync('node', [HOOK], {
        input: JSON.stringify({
          tool_name: 'Bash',
          tool_input: { command: 'gh pr view 999' },
          tool_response: 'PR data',
          session_id
        }),
        env: freshEnv(tmpDir),
        timeout: 10000
      }).toString();
    } catch (e) {
      stdout = e.stdout ? e.stdout.toString() : '';
      if (e.status !== 0) throw e;
    }
    assert.ok(!stdout.includes('"block"'), `Bash should NOT block, got: ${stdout}`);

    const finalState = JSON.parse(fs.readFileSync(path.join(stateDir, 'loop-detector.json'), 'utf8'));
    assert.equal(finalState.np_count, 0, 'Bash should reset np_count to 0');
  });

  test('ETE: pre-seeded np_count=4 + WebSearch call DOES block (true-stall preserved)', () => {
    const tmpDir = makeTmpDir();
    tmpDirs.push(tmpDir);
    const session_id = uniqueSession();
    const stateDir = path.join(tmpDir, '.mercury', 'state');
    fs.mkdirSync(stateDir, { recursive: true });
    fs.writeFileSync(path.join(stateDir, 'loop-detector.json'), JSON.stringify({
      session_id,
      dup_count: 0, dup_tool: null, dup_hash: null,
      err_count: 0, err_last: null,
      read_count: 0, np_count: 4,
      last_activity_ts: Date.now(), last_write_ts: Date.now()
    }, null, 2));

    let stdout = '';
    try {
      stdout = execFileSync('node', [HOOK], {
        input: JSON.stringify({
          tool_name: 'WebSearch',
          tool_input: { query: 'docs' },
          tool_response: 'results',
          session_id
        }),
        env: freshEnv(tmpDir),
        timeout: 10000
      }).toString();
    } catch (e) {
      stdout = e.stdout ? e.stdout.toString() : '';
      if (e.status !== 0) throw e;
    }
    assert.ok(stdout.includes('"block"'), `WebSearch SHOULD block, got: ${stdout}`);
    assert.ok(stdout.includes('no_progress'), 'block reason should be no_progress');
  });
});

// ── 14. hook.cjs end-to-end integration ─────────────────────────────────────

describe('hook.cjs end-to-end integration', () => {
  const { execFileSync } = require('child_process');
  const HOOK = path.join(__dirname, 'hook.cjs');
  let counter = 0;
  let tmpDirs = [];

  function freshEnv(tmpDir) {
    return { ...process.env, CLAUDE_PROJECT_DIR: tmpDir };
  }

  function uniqueSession() {
    return `ete-session-${process.pid}-${++counter}-${Date.now()}`;
  }

  afterEach(() => {
    for (const d of tmpDirs) {
      try { fs.rmSync(d, { recursive: true, force: true }); } catch { /* ignore */ }
    }
    tmpDirs = [];
  });

  test('ETE happy path: normal tool call exits 0 with no block output', () => {
    const tmpDir = makeTmpDir();
    tmpDirs.push(tmpDir);
    const payload = JSON.stringify({
      tool_name: 'Read',
      tool_input: { file_path: '/some/file.txt' },
      tool_response: 'file contents here',
      session_id: uniqueSession()
    });
    let stdout;
    try {
      stdout = execFileSync('node', [HOOK], {
        input: payload,
        env: freshEnv(tmpDir),
        timeout: 10000
      }).toString();
    } catch (e) {
      // exit code 0 with block output also lands here if stdout non-empty
      stdout = e.stdout ? e.stdout.toString() : '';
      // Re-throw if process actually errored (non-zero exit for unexpected reasons)
      if (e.status !== 0) throw e;
    }
    // Happy path: no block decision in stdout
    assert.ok(!stdout.includes('"block"'), `unexpected block output: ${stdout}`);
  });

  test('ETE stall trigger: no_progress threshold reached causes block with report', () => {
    const tmpDir = makeTmpDir();
    tmpDirs.push(tmpDir);
    const session_id = uniqueSession();
    const stateDir = path.join(tmpDir, '.mercury', 'state');
    fs.mkdirSync(stateDir, { recursive: true });

    // Pre-seed np_count at threshold-1 (default=5, seed at 4).
    // Sending a non-read/non-write/non-progress/non-error tool call increments np_count to 5 → block.
    // WebSearch is the canonical true-stall pattern (research without artifacts).
    // NOTE: Bash/Agent/Task*/Skill/ToolSearch are now in PROGRESS_TOOLS (Issue #325) and reset np_count.
    const preState = {
      session_id,
      dup_count: 0, dup_tool: null, dup_hash: null,
      err_count: 0, err_last: null,
      read_count: 0, np_count: 4,
      last_activity_ts: Date.now(), last_write_ts: Date.now()
    };
    fs.writeFileSync(path.join(stateDir, 'loop-detector.json'), JSON.stringify(preState, null, 2));

    // WebSearch: is_write=false, is_read=false, is_progress=false, errored=false → np_count++
    const payload = JSON.stringify({
      tool_name: 'WebSearch',
      tool_input: { query: 'mercury loop detector' },
      tool_response: 'Search results: 10 hits',
      session_id
    });

    let stdout = '';
    let exitCode = 0;
    try {
      stdout = execFileSync('node', [HOOK], {
        input: payload,
        env: freshEnv(tmpDir),
        timeout: 10000
      }).toString();
    } catch (e) {
      stdout = e.stdout ? e.stdout.toString() : '';
      exitCode = e.status ?? 0;
    }

    // hook exits 0 even on block (process.exit(0) in block())
    assert.equal(exitCode, 0, 'hook should exit 0 even on block');
    assert.ok(stdout.includes('"block"'), `expected block decision in stdout, got: ${stdout}`);

    // Verify stall report written
    const reportDir = path.join(tmpDir, '.mercury', 'state', 'stall-reports');
    assert.ok(fs.existsSync(reportDir), 'stall-reports dir should exist');
    const reports = fs.readdirSync(reportDir).filter(f => f.endsWith('.json'));
    assert.ok(reports.length >= 1, 'at least one report should be written');
    const report = JSON.parse(fs.readFileSync(path.join(reportDir, reports[0]), 'utf8'));
    assert.equal(report.session_id, session_id);
    assert.equal(report.stall_type, 'no_progress');
    // stall_reason must be the full block reason string, not the short internal reason
    assert.ok(report.stall_reason.startsWith('Mercury loop detector: '),
      `stall_reason should start with prefix, got: ${report.stall_reason}`);
    assert.ok(report.stall_reason.includes('Buffer reset.'),
      `stall_reason should include "Buffer reset.", got: ${report.stall_reason}`);
  });
});

// ── 15. last_progress_ts (Issue #372 sister-fix to #325) ────────────────────

describe('last_progress_ts: timeout uses progress signal, not just write', () => {
  test('updateTimestamps bumps last_progress_ts on progress (no write)', () => {
    const now   = 1_700_000_000_000;
    const state = makeState({ last_write_ts: now - 600_000, last_progress_ts: now - 600_000 });
    updateTimestamps(state, /*is_write*/ false, /*is_progress*/ true, now);
    assert.equal(state.last_progress_ts, now, 'progress call must bump last_progress_ts');
    assert.equal(state.last_write_ts, now - 600_000, 'last_write_ts unchanged on non-write');
    assert.equal(state.last_activity_ts, now);
  });

  test('updateTimestamps bumps last_progress_ts on write', () => {
    const now   = 1_700_000_000_000;
    const state = makeState({ last_write_ts: now - 600_000, last_progress_ts: now - 600_000 });
    updateTimestamps(state, /*is_write*/ true, /*is_progress*/ false, now);
    assert.equal(state.last_progress_ts, now);
    assert.equal(state.last_write_ts, now);
  });

  test('updateTimestamps does NOT bump last_progress_ts on read-only/no-progress', () => {
    const now   = 1_700_000_000_000;
    const state = makeState({ last_write_ts: now - 600_000, last_progress_ts: now - 600_000 });
    updateTimestamps(state, /*is_write*/ false, /*is_progress*/ false, now);
    assert.equal(state.last_progress_ts, now - 600_000, 'non-progress non-write must NOT bump');
    assert.equal(state.last_activity_ts, now, 'last_activity_ts always bumps');
  });

  test('updateTimestamps initialises last_progress_ts from last_write_ts on first call (backward-compat)', () => {
    const now   = 1_700_000_000_000;
    // Old state file from pre-#372: last_progress_ts absent (null), last_write_ts set
    const state = makeState({ last_write_ts: now - 100_000, last_progress_ts: null });
    updateTimestamps(state, false, false, now);
    assert.equal(state.last_progress_ts, now - 100_000,
      'last_progress_ts must initialise from last_write_ts when absent');
  });

  test('checkMultiLevel uses last_progress_ts when present', () => {
    const now   = Date.now();
    // last_write_ts very old (would trigger hard), last_progress_ts recent → no timeout
    const state = makeState({ last_write_ts: now - 5_671_000, last_progress_ts: now - 30_000 });
    const cfg   = makeCfg({ timeout_soft_sec: 60, timeout_idle_sec: 300, timeout_hard_sec: 900 });
    const result = checkMultiLevel(state, cfg, now);
    assert.equal(result, null, 'recent last_progress_ts must override stale last_write_ts');
  });

  test('checkMultiLevel falls back to last_write_ts when last_progress_ts is null (backward-compat)', () => {
    const now   = Date.now();
    const state = makeState({ last_write_ts: now - 910_000, last_progress_ts: null });
    const cfg   = makeCfg({ timeout_soft_sec: 60, timeout_idle_sec: 300, timeout_hard_sec: 900 });
    const result = checkMultiLevel(state, cfg, now);
    assert.ok(result, 'must return result via last_write_ts fallback');
    assert.equal(result.level, 'hard');
    assert.equal(result.should_block, true);
  });

  test('checkMultiLevel returns null when both timestamps are null', () => {
    const now   = Date.now();
    const state = makeState({ last_write_ts: null, last_progress_ts: null });
    const result = checkMultiLevel(state, makeCfg(), now);
    assert.equal(result, null);
  });

  test('checkMultiLevel rejects last_progress_ts=0 (polluted state) and falls back to last_write_ts', () => {
    const now   = Date.now();
    // Polluted: last_progress_ts=0 would be Number.isFinite=true; without > 0 guard
    // checkMultiLevel would treat it as ref → elapsed ≈ now → instant hard-timeout.
    // Use last_write_ts within soft threshold (60s default) so fallback yields null.
    const state = makeState({ last_write_ts: now - 30_000, last_progress_ts: 0 });
    const result = checkMultiLevel(state, makeCfg(), now);
    assert.equal(result, null, '0 must not be treated as valid; fallback to recent last_write_ts');
  });

  test('checkMultiLevel rejects last_progress_ts=-1 (negative) and falls back', () => {
    const now   = Date.now();
    const state = makeState({ last_write_ts: now - 30_000, last_progress_ts: -1 });
    const result = checkMultiLevel(state, makeCfg(), now);
    assert.equal(result, null, 'negative ts must not be treated as valid');
  });

  test('checkMultiLevel returns null when both ts are 0/negative (no fallback target)', () => {
    const now   = Date.now();
    const state = makeState({ last_write_ts: 0, last_progress_ts: 0 });
    const result = checkMultiLevel(state, makeCfg(), now);
    assert.equal(result, null, 'no positive ts → return null, do not block');
  });

  test('updateTimestamps heals last_progress_ts=0 polluted state on init', () => {
    const now   = Date.now();
    const state = makeState({ last_write_ts: now - 100_000, last_progress_ts: 0 });
    updateTimestamps(state, false, false, now);
    assert.equal(state.last_progress_ts, now - 100_000,
      '0 is rejected, init from last_write_ts');
  });

  test('updateTimestamps heals last_write_ts=0 polluted state on init', () => {
    const now   = Date.now();
    const state = makeState({ last_write_ts: 0, last_progress_ts: 0 });
    updateTimestamps(state, false, false, now);
    assert.equal(state.last_write_ts, now, '0 last_write_ts is healed to now');
    assert.equal(state.last_progress_ts, now, 'last_progress_ts inherits healed last_write_ts');
  });

  test('checkMultiLevel rejects future-dated last_progress_ts (clock skew/pollution) and falls back', () => {
    const now   = Date.now();
    // Polluted: last_progress_ts in the future. Without future-ts guard, elapsed<0
    // returns null → silent permanent bypass. Should fall back to last_write_ts.
    const state = makeState({ last_write_ts: now - 910_000, last_progress_ts: now + 999_999_999 });
    const cfg   = makeCfg({ timeout_soft_sec: 60, timeout_idle_sec: 300, timeout_hard_sec: 900 });
    const result = checkMultiLevel(state, cfg, now);
    assert.ok(result, 'future-dated last_progress_ts must not bypass timeout — fall back to last_write_ts');
    assert.equal(result.level, 'hard', 'fallback ref triggers hard-timeout');
    assert.equal(result.should_block, true);
  });

  test('checkMultiLevel returns null when both ts are future-dated (no valid ref)', () => {
    const now   = Date.now();
    const state = makeState({ last_write_ts: now + 60_000_000, last_progress_ts: now + 999_999_999 });
    const result = checkMultiLevel(state, makeCfg(), now);
    assert.equal(result, null, 'no valid past ts → return null, do not block');
  });

  test('checkMultiLevel tolerates 60s forward grace (Date.now race within hook fire)', () => {
    const now   = Date.now();
    // 30s in the future — within FUTURE_TS_GRACE_MS, treat as valid
    const state = makeState({ last_progress_ts: now + 30_000 });
    const result = checkMultiLevel(state, makeCfg(), now);
    assert.equal(result, null, '30s forward grace tolerated; elapsed<0 returns null per pre-existing guard');
  });

  test('updateTimestamps heals future-dated last_progress_ts on init (Argus iter-3 Copilot)', () => {
    const now   = Date.now();
    // Polluted: last_progress_ts far in the future. Without healing, it would
    // persist forever — checkMultiLevel rejects it but state file stays bad.
    const state = makeState({ last_write_ts: now - 100_000, last_progress_ts: now + 999_999_999 });
    updateTimestamps(state, false, false, now);
    assert.equal(state.last_progress_ts, now - 100_000,
      'future-dated last_progress_ts must be healed to last_write_ts');
  });

  test('updateTimestamps heals both future-dated timestamps on init', () => {
    const now   = Date.now();
    const state = makeState({ last_write_ts: now + 99_999_999, last_progress_ts: now + 999_999_999 });
    updateTimestamps(state, false, false, now);
    assert.equal(state.last_write_ts, now, 'future-dated last_write_ts healed to now');
    assert.equal(state.last_progress_ts, now, 'last_progress_ts inherits healed last_write_ts');
  });
});

// ── 16. ETE: long Bash/Skill phase no longer trips hard-timeout (Issue #372) ──

describe('hook.cjs ETE: long PROGRESS_TOOLS phase suppresses hard-timeout', () => {
  const { execFileSync } = require('child_process');
  const HOOK = path.join(__dirname, 'hook.cjs');
  let counter = 0;
  let tmpDirs = [];

  function freshEnv(tmpDir) { return { ...process.env, CLAUDE_PROJECT_DIR: tmpDir }; }
  function uniqueSession()  { return `ete-progts-${process.pid}-${++counter}-${Date.now()}`; }

  afterEach(() => {
    for (const d of tmpDirs) {
      try { fs.rmSync(d, { recursive: true, force: true }); } catch { /* ignore */ }
    }
    tmpDirs = [];
  });

  test('ETE: stale last_write_ts (5671s ago) + recent last_progress_ts + Read call does NOT block (persisted-path load-bearing)', () => {
    const tmpDir = makeTmpDir();
    tmpDirs.push(tmpDir);
    const session_id = uniqueSession();
    const stateDir = path.join(tmpDir, '.mercury', 'state');
    fs.mkdirSync(stateDir, { recursive: true });

    const now = Date.now();
    // Reproduces S95 stall report scenario (last_tool=Read, np_count=0, last_write_ts
    // 5671s old). Read is non-write AND non-progress, so updateTimestamps does NOT
    // bump last_progress_ts — the preseeded recent value MUST be load-bearing.
    // A regression in loadState (e.g. dropping last_progress_ts on read) would let
    // last_write_ts (5671s old) become the ref → hard-timeout → this test fails.
    fs.writeFileSync(path.join(stateDir, 'loop-detector.json'), JSON.stringify({
      session_id,
      dup_count: 0, dup_tool: null, dup_hash: null,
      err_count: 0, err_last: null,
      read_count: 0, np_count: 0,
      last_activity_ts: now - 1_000,
      last_write_ts:    now - 5_671_000,
      last_progress_ts: now - 30_000
    }, null, 2));

    let stdout = '';
    try {
      stdout = execFileSync('node', [HOOK], {
        input: JSON.stringify({
          tool_name: 'Read',
          tool_input: { file_path: '/some/file.txt' },
          tool_response: 'file contents',
          session_id
        }),
        env: freshEnv(tmpDir),
        timeout: 10000
      }).toString();
    } catch (e) {
      stdout = e.stdout ? e.stdout.toString() : '';
      if (e.status !== 0) throw e;
    }
    assert.ok(!stdout.includes('"block"'),
      `Read with recent persisted last_progress_ts must NOT block (proves load path), got: ${stdout}`);

    const finalState = JSON.parse(fs.readFileSync(path.join(stateDir, 'loop-detector.json'), 'utf8'));
    // Read does NOT bump last_progress_ts; persisted value must be unchanged.
    assert.equal(finalState.last_progress_ts, now - 30_000,
      'Read must NOT bump last_progress_ts (only PROGRESS_TOOLS or write do)');
    assert.equal(finalState.last_write_ts, now - 5_671_000,
      'Read must NOT bump last_write_ts');
  });

  test('ETE: stale last_write_ts (5671s ago) + recent last_progress_ts + Bash call does NOT block AND bumps last_progress_ts', () => {
    const tmpDir = makeTmpDir();
    tmpDirs.push(tmpDir);
    const session_id = uniqueSession();
    const stateDir = path.join(tmpDir, '.mercury', 'state');
    fs.mkdirSync(stateDir, { recursive: true });

    const now = Date.now();
    // Documents that PROGRESS_TOOLS calls (Bash here) bump last_progress_ts —
    // complementary to the Read test above which validates the persisted path.
    fs.writeFileSync(path.join(stateDir, 'loop-detector.json'), JSON.stringify({
      session_id,
      dup_count: 0, dup_tool: null, dup_hash: null,
      err_count: 0, err_last: null,
      read_count: 0, np_count: 0,
      last_activity_ts: now - 1_000,
      last_write_ts:    now - 5_671_000,
      last_progress_ts: now - 30_000
    }, null, 2));

    let stdout = '';
    try {
      stdout = execFileSync('node', [HOOK], {
        input: JSON.stringify({
          tool_name: 'Bash',
          tool_input: { command: 'gh pr view 999' },
          tool_response: 'PR data',
          session_id
        }),
        env: freshEnv(tmpDir),
        timeout: 10000
      }).toString();
    } catch (e) {
      stdout = e.stdout ? e.stdout.toString() : '';
      if (e.status !== 0) throw e;
    }
    assert.ok(!stdout.includes('"block"'),
      `Bash with recent last_progress_ts must NOT block, got: ${stdout}`);

    const finalState = JSON.parse(fs.readFileSync(path.join(stateDir, 'loop-detector.json'), 'utf8'));
    // Relative invariant (Argus iter-2 body advisory): must be strictly newer than
    // the preseeded value (now - 30_000ms). Avoids wall-clock-window flakiness on
    // CI load — only requires the bump operation actually happened.
    const preseededProgressTs = now - 30_000;
    assert.ok(Number.isFinite(finalState.last_progress_ts) && finalState.last_progress_ts > preseededProgressTs,
      'Bash (in PROGRESS_TOOLS) must bump last_progress_ts past the preseeded value');
  });

  test('ETE: WebSearch loop with old last_progress_ts STILL blocks (true-stall preserved)', () => {
    const tmpDir = makeTmpDir();
    tmpDirs.push(tmpDir);
    const session_id = uniqueSession();
    const stateDir = path.join(tmpDir, '.mercury', 'state');
    fs.mkdirSync(stateDir, { recursive: true });

    const now = Date.now();
    fs.writeFileSync(path.join(stateDir, 'loop-detector.json'), JSON.stringify({
      session_id,
      dup_count: 0, dup_tool: null, dup_hash: null,
      err_count: 0, err_last: null,
      read_count: 0, np_count: 0,
      last_activity_ts: now - 1_000,
      last_write_ts:    now - 1_000_000,
      last_progress_ts: now - 1_000_000  // no PROGRESS_TOOLS for >900s
    }, null, 2));

    let stdout = '';
    try {
      stdout = execFileSync('node', [HOOK], {
        input: JSON.stringify({
          tool_name: 'WebSearch',
          tool_input: { query: 'docs' },
          tool_response: 'results',
          session_id
        }),
        env: freshEnv(tmpDir),
        timeout: 10000
      }).toString();
    } catch (e) {
      stdout = e.stdout ? e.stdout.toString() : '';
      if (e.status !== 0) throw e;
    }
    assert.ok(stdout.includes('"block"'),
      `WebSearch with stale last_progress_ts SHOULD still block (true-stall), got: ${stdout}`);
    assert.ok(stdout.includes('hard timeout'), 'block reason should mention hard timeout');
  });

  test('ETE: backward-compat — old state without last_progress_ts uses last_write_ts fallback', () => {
    const tmpDir = makeTmpDir();
    tmpDirs.push(tmpDir);
    const session_id = uniqueSession();
    const stateDir = path.join(tmpDir, '.mercury', 'state');
    fs.mkdirSync(stateDir, { recursive: true });

    const now = Date.now();
    // Old state from pre-#372 deployment: no last_progress_ts field at all
    fs.writeFileSync(path.join(stateDir, 'loop-detector.json'), JSON.stringify({
      session_id,
      dup_count: 0, dup_tool: null, dup_hash: null,
      err_count: 0, err_last: null,
      read_count: 0, np_count: 0,
      last_activity_ts: now - 1_000,
      last_write_ts:    now - 1_000  // recent → no timeout
      // last_progress_ts intentionally absent
    }, null, 2));

    let stdout = '';
    try {
      stdout = execFileSync('node', [HOOK], {
        input: JSON.stringify({
          tool_name: 'Read',
          tool_input: { file_path: '/some/file.txt' },
          tool_response: 'contents',
          session_id
        }),
        env: freshEnv(tmpDir),
        timeout: 10000
      }).toString();
    } catch (e) {
      stdout = e.stdout ? e.stdout.toString() : '';
      if (e.status !== 0) throw e;
    }
    assert.ok(!stdout.includes('"block"'),
      `Old state file must not break hook; got: ${stdout}`);

    // Hook must initialise last_progress_ts on save
    const finalState = JSON.parse(fs.readFileSync(path.join(stateDir, 'loop-detector.json'), 'utf8'));
    assert.ok(Number.isFinite(finalState.last_progress_ts),
      'hook must populate last_progress_ts on first save (backward-compat init)');
  });
});

// ── 18. read_write_ratio default + research-mode opt-out (Issue #306) ────────

describe('Issue #306: read_write_ratio default raised to 12 + research-mode env var', () => {
  test('default config shape: read_write_ratio_threshold === 12', () => {
    // Read DEFAULTS via the source-of-truth: spawn hook with no config + check
    // a borderline state. read_count=11 (< 12) must NOT trigger; read_count=12 MUST.
    // This is end-to-end so it covers DEFAULTS + clampInt + detectStall together.
    const { execFileSync } = require('child_process');
    const HOOK = path.join(__dirname, 'hook.cjs');
    const tmpDir = makeTmpDir();

    try {
      const session_id = `ete-rwt-default-${process.pid}-${Date.now()}`;
      const stateDir = path.join(tmpDir, '.mercury', 'state');
      fs.mkdirSync(stateDir, { recursive: true });
      fs.writeFileSync(path.join(stateDir, 'loop-detector.json'), JSON.stringify({
        session_id,
        dup_count: 0, dup_tool: null, dup_hash: null,
        err_count: 0, err_last: null,
        read_count: 11, np_count: 0,
        last_activity_ts: Date.now(), last_write_ts: Date.now(),
        last_progress_ts: Date.now()
      }, null, 2));

      // 12th Read brings read_count to 12 → must trigger (boundary at threshold)
      let stdout = '';
      const env = { ...process.env, CLAUDE_PROJECT_DIR: tmpDir };
      delete env.MERCURY_LOOP_DETECTOR_MODE;
      try {
        stdout = execFileSync('node', [HOOK], {
          input: JSON.stringify({
            tool_name: 'Read',
            tool_input: { file_path: '/x.txt' },
            tool_response: 'ok',
            session_id
          }),
          env,
          timeout: 10000
        }).toString();
      } catch (e) {
        stdout = e.stdout ? e.stdout.toString() : '';
        if (e.status !== 0) throw e;
      }
      assert.ok(stdout.includes('"block"'),
        `12th Read must trigger read_write_ratio at default threshold 12, got: ${stdout}`);
      assert.ok(stdout.includes('read_write_ratio'),
        `block reason must be read_write_ratio, got: ${stdout}`);
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });

  test('default threshold: read_count=10 → 11th Read does NOT trigger (below 12)', () => {
    const { execFileSync } = require('child_process');
    const HOOK = path.join(__dirname, 'hook.cjs');
    const tmpDir = makeTmpDir();

    try {
      const session_id = `ete-rwt-below-${process.pid}-${Date.now()}`;
      const stateDir = path.join(tmpDir, '.mercury', 'state');
      fs.mkdirSync(stateDir, { recursive: true });
      fs.writeFileSync(path.join(stateDir, 'loop-detector.json'), JSON.stringify({
        session_id,
        dup_count: 0, dup_tool: null, dup_hash: null,
        err_count: 0, err_last: null,
        read_count: 10, np_count: 0,
        last_activity_ts: Date.now(), last_write_ts: Date.now(),
        last_progress_ts: Date.now()
      }, null, 2));

      let stdout = '';
      const env = { ...process.env, CLAUDE_PROJECT_DIR: tmpDir };
      delete env.MERCURY_LOOP_DETECTOR_MODE;
      try {
        stdout = execFileSync('node', [HOOK], {
          input: JSON.stringify({
            tool_name: 'Read',
            tool_input: { file_path: '/x.txt' },
            tool_response: 'ok',
            session_id
          }),
          env,
          timeout: 10000
        }).toString();
      } catch (e) {
        stdout = e.stdout ? e.stdout.toString() : '';
        if (e.status !== 0) throw e;
      }
      assert.ok(!stdout.includes('"block"'),
        `11th consecutive Read (read_count=11) must NOT trigger at threshold 12, got: ${stdout}`);
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });

  test('MERCURY_LOOP_DETECTOR_MODE=research disables read_write_ratio block (large burst tolerated)', () => {
    const { execFileSync } = require('child_process');
    const HOOK = path.join(__dirname, 'hook.cjs');
    const tmpDir = makeTmpDir();

    try {
      const session_id = `ete-rwt-research-${process.pid}-${Date.now()}`;
      const stateDir = path.join(tmpDir, '.mercury', 'state');
      fs.mkdirSync(stateDir, { recursive: true });
      // Pre-seed read_count well past default 12 to prove research mode bypasses
      fs.writeFileSync(path.join(stateDir, 'loop-detector.json'), JSON.stringify({
        session_id,
        dup_count: 0, dup_tool: null, dup_hash: null,
        err_count: 0, err_last: null,
        read_count: 99, np_count: 0,
        last_activity_ts: Date.now(), last_write_ts: Date.now(),
        last_progress_ts: Date.now()
      }, null, 2));

      let stdout = '';
      const env = { ...process.env, CLAUDE_PROJECT_DIR: tmpDir, MERCURY_LOOP_DETECTOR_MODE: 'research' };
      try {
        stdout = execFileSync('node', [HOOK], {
          input: JSON.stringify({
            tool_name: 'Glob',
            tool_input: { pattern: '**/*.md' },
            tool_response: 'matches',
            session_id
          }),
          env,
          timeout: 10000
        }).toString();
      } catch (e) {
        stdout = e.stdout ? e.stdout.toString() : '';
        if (e.status !== 0) throw e;
      }
      assert.ok(!stdout.includes('"block"'),
        `research mode must suppress read_write_ratio even at read_count=100, got: ${stdout}`);
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });

  test('MERCURY_LOOP_DETECTOR_MODE=research does NOT disable duplicate_call heuristic', () => {
    const { execFileSync } = require('child_process');
    const HOOK = path.join(__dirname, 'hook.cjs');
    const tmpDir = makeTmpDir();

    try {
      const session_id = `ete-rwt-dup-research-${process.pid}-${Date.now()}`;
      const stateDir = path.join(tmpDir, '.mercury', 'state');
      fs.mkdirSync(stateDir, { recursive: true });
      // Pre-seed at duplicate_call boundary; the next identical Bash hits threshold=3
      fs.writeFileSync(path.join(stateDir, 'loop-detector.json'), JSON.stringify({
        session_id,
        dup_count: 2, dup_tool: 'Bash', dup_hash: '5c8b1d3a',
        err_count: 0, err_last: null,
        read_count: 0, np_count: 0,
        last_activity_ts: Date.now(), last_write_ts: Date.now(),
        last_progress_ts: Date.now()
      }, null, 2));

      // The hash of the input below must equal '5c8b1d3a' for duplicate to fire.
      // Compute the actual hash by hashing the same way hook.cjs does.
      const sameInput = { command: 'gh pr view 999' };
      const realHash = crypto.createHash('sha256')
        .update(JSON.stringify(sameInput)).digest('hex').slice(0, 8);
      // Re-seed with the actual hash so the test is portable
      fs.writeFileSync(path.join(stateDir, 'loop-detector.json'), JSON.stringify({
        session_id,
        dup_count: 2, dup_tool: 'Bash', dup_hash: realHash,
        err_count: 0, err_last: null,
        read_count: 0, np_count: 0,
        last_activity_ts: Date.now(), last_write_ts: Date.now(),
        last_progress_ts: Date.now()
      }, null, 2));

      let stdout = '';
      const env = { ...process.env, CLAUDE_PROJECT_DIR: tmpDir, MERCURY_LOOP_DETECTOR_MODE: 'research' };
      try {
        stdout = execFileSync('node', [HOOK], {
          input: JSON.stringify({
            tool_name: 'Bash',
            tool_input: sameInput,
            tool_response: 'ok',
            session_id
          }),
          env,
          timeout: 10000
        }).toString();
      } catch (e) {
        stdout = e.stdout ? e.stdout.toString() : '';
        if (e.status !== 0) throw e;
      }
      assert.ok(stdout.includes('"block"'),
        `research mode must NOT disable duplicate_call heuristic, got: ${stdout}`);
      assert.ok(stdout.includes('duplicate_call'),
        `block reason must be duplicate_call, got: ${stdout}`);
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });

  test('MERCURY_LOOP_DETECTOR_MODE unset/empty → default threshold (12) still active', () => {
    const { execFileSync } = require('child_process');
    const HOOK = path.join(__dirname, 'hook.cjs');
    const tmpDir = makeTmpDir();

    try {
      const session_id = `ete-rwt-emptyenv-${process.pid}-${Date.now()}`;
      const stateDir = path.join(tmpDir, '.mercury', 'state');
      fs.mkdirSync(stateDir, { recursive: true });
      fs.writeFileSync(path.join(stateDir, 'loop-detector.json'), JSON.stringify({
        session_id,
        dup_count: 0, dup_tool: null, dup_hash: null,
        err_count: 0, err_last: null,
        read_count: 11, np_count: 0,
        last_activity_ts: Date.now(), last_write_ts: Date.now(),
        last_progress_ts: Date.now()
      }, null, 2));

      let stdout = '';
      // Pass MERCURY_LOOP_DETECTOR_MODE='' (empty string, NOT 'research')
      const env = { ...process.env, CLAUDE_PROJECT_DIR: tmpDir, MERCURY_LOOP_DETECTOR_MODE: '' };
      try {
        stdout = execFileSync('node', [HOOK], {
          input: JSON.stringify({
            tool_name: 'Read',
            tool_input: { file_path: '/x.txt' },
            tool_response: 'ok',
            session_id
          }),
          env,
          timeout: 10000
        }).toString();
      } catch (e) {
        stdout = e.stdout ? e.stdout.toString() : '';
        if (e.status !== 0) throw e;
      }
      assert.ok(stdout.includes('"block"'),
        `empty env var must NOT enable research mode; default 12 must trigger at read_count=12, got: ${stdout}`);
      assert.ok(stdout.includes('read_write_ratio'),
        `block reason must be read_write_ratio, got: ${stdout}`);
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });

  test('MERCURY_LOOP_DETECTOR_MODE=other (non-research value) → research mode NOT activated', () => {
    const { execFileSync } = require('child_process');
    const HOOK = path.join(__dirname, 'hook.cjs');
    const tmpDir = makeTmpDir();

    try {
      const session_id = `ete-rwt-otherval-${process.pid}-${Date.now()}`;
      const stateDir = path.join(tmpDir, '.mercury', 'state');
      fs.mkdirSync(stateDir, { recursive: true });
      fs.writeFileSync(path.join(stateDir, 'loop-detector.json'), JSON.stringify({
        session_id,
        dup_count: 0, dup_tool: null, dup_hash: null,
        err_count: 0, err_last: null,
        read_count: 11, np_count: 0,
        last_activity_ts: Date.now(), last_write_ts: Date.now(),
        last_progress_ts: Date.now()
      }, null, 2));

      let stdout = '';
      const env = { ...process.env, CLAUDE_PROJECT_DIR: tmpDir, MERCURY_LOOP_DETECTOR_MODE: 'debug' };
      try {
        stdout = execFileSync('node', [HOOK], {
          input: JSON.stringify({
            tool_name: 'Read',
            tool_input: { file_path: '/x.txt' },
            tool_response: 'ok',
            session_id
          }),
          env,
          timeout: 10000
        }).toString();
      } catch (e) {
        stdout = e.stdout ? e.stdout.toString() : '';
        if (e.status !== 0) throw e;
      }
      assert.ok(stdout.includes('"block"'),
        `non-'research' mode value must NOT enable opt-out; default 12 still active, got: ${stdout}`);
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });

  test('MERCURY_LOOP_DETECTOR_MODE=Research (case mismatch) → strict-eq rejects, default 12 active', () => {
    // Locks the strict-eq behavior (=== 'research'). Capitalized 'Research' must
    // NOT activate opt-out — guards against ambiguous casing in user docs/aliases.
    const { execFileSync } = require('child_process');
    const HOOK = path.join(__dirname, 'hook.cjs');
    const tmpDir = makeTmpDir();

    try {
      const session_id = `ete-rwt-case-${process.pid}-${Date.now()}`;
      const stateDir = path.join(tmpDir, '.mercury', 'state');
      fs.mkdirSync(stateDir, { recursive: true });
      fs.writeFileSync(path.join(stateDir, 'loop-detector.json'), JSON.stringify({
        session_id,
        dup_count: 0, dup_tool: null, dup_hash: null,
        err_count: 0, err_last: null,
        read_count: 11, np_count: 0,
        last_activity_ts: Date.now(), last_write_ts: Date.now(),
        last_progress_ts: Date.now()
      }, null, 2));

      let stdout = '';
      const env = { ...process.env, CLAUDE_PROJECT_DIR: tmpDir, MERCURY_LOOP_DETECTOR_MODE: 'Research' };
      try {
        stdout = execFileSync('node', [HOOK], {
          input: JSON.stringify({
            tool_name: 'Read', tool_input: { file_path: '/x.txt' },
            tool_response: 'ok', session_id
          }),
          env, timeout: 10000
        }).toString();
      } catch (e) {
        stdout = e.stdout ? e.stdout.toString() : '';
        if (e.status !== 0) throw e;
      }
      assert.ok(stdout.includes('"block"'),
        `case-mismatched 'Research' must NOT enable opt-out; default 12 still active, got: ${stdout}`);
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });

  test("MERCURY_LOOP_DETECTOR_MODE=' research ' (whitespace) → strict-eq rejects, default 12 active", () => {
    // Locks no-trim behavior. Users who paste 'research ' with trailing space
    // must NOT silently enable opt-out — fail-closed guards production sessions
    // from accidental heuristic disablement.
    const { execFileSync } = require('child_process');
    const HOOK = path.join(__dirname, 'hook.cjs');
    const tmpDir = makeTmpDir();

    try {
      const session_id = `ete-rwt-ws-${process.pid}-${Date.now()}`;
      const stateDir = path.join(tmpDir, '.mercury', 'state');
      fs.mkdirSync(stateDir, { recursive: true });
      fs.writeFileSync(path.join(stateDir, 'loop-detector.json'), JSON.stringify({
        session_id,
        dup_count: 0, dup_tool: null, dup_hash: null,
        err_count: 0, err_last: null,
        read_count: 11, np_count: 0,
        last_activity_ts: Date.now(), last_write_ts: Date.now(),
        last_progress_ts: Date.now()
      }, null, 2));

      let stdout = '';
      const env = { ...process.env, CLAUDE_PROJECT_DIR: tmpDir, MERCURY_LOOP_DETECTOR_MODE: ' research ' };
      try {
        stdout = execFileSync('node', [HOOK], {
          input: JSON.stringify({
            tool_name: 'Read', tool_input: { file_path: '/x.txt' },
            tool_response: 'ok', session_id
          }),
          env, timeout: 10000
        }).toString();
      } catch (e) {
        stdout = e.stdout ? e.stdout.toString() : '';
        if (e.status !== 0) throw e;
      }
      assert.ok(stdout.includes('"block"'),
        `whitespace-padded ' research ' must NOT enable opt-out; default 12 still active, got: ${stdout}`);
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });

  test('research → default mid-session: pre-research read accumulation is reset, no instant block', () => {
    // Codex dual-verify M finding: env var is per-invocation; if user enables
    // research mode after read_count already accumulated, OR runs many reads
    // under research and then disables it, the persisted state.read_count must
    // not cause an immediate block on the very next default-mode read.
    // main() resets state.read_count = 0 when research mode is on, so any reads
    // after disabling start fresh from 0.
    const { execFileSync } = require('child_process');
    const HOOK = path.join(__dirname, 'hook.cjs');
    const tmpDir = makeTmpDir();

    try {
      const session_id = `ete-rwt-toggle-${process.pid}-${Date.now()}`;
      const stateDir = path.join(tmpDir, '.mercury', 'state');
      fs.mkdirSync(stateDir, { recursive: true });
      // Pre-seed read_count above default threshold (would block if not reset)
      fs.writeFileSync(path.join(stateDir, 'loop-detector.json'), JSON.stringify({
        session_id,
        dup_count: 0, dup_tool: null, dup_hash: null,
        err_count: 0, err_last: null,
        read_count: 50, np_count: 0,
        last_activity_ts: Date.now(), last_write_ts: Date.now(),
        last_progress_ts: Date.now()
      }, null, 2));

      // Step 1: invocation under research mode — must NOT block, must reset read_count
      let stdout = '';
      const envResearch = { ...process.env, CLAUDE_PROJECT_DIR: tmpDir, MERCURY_LOOP_DETECTOR_MODE: 'research' };
      try {
        stdout = execFileSync('node', [HOOK], {
          input: JSON.stringify({
            tool_name: 'Read', tool_input: { file_path: '/x.txt' },
            tool_response: 'ok', session_id
          }),
          env: envResearch, timeout: 10000
        }).toString();
      } catch (e) {
        stdout = e.stdout ? e.stdout.toString() : '';
        if (e.status !== 0) throw e;
      }
      assert.ok(!stdout.includes('"block"'),
        `research-mode invocation must NOT block on pre-seeded read_count=50, got: ${stdout}`);
      const midState = JSON.parse(fs.readFileSync(path.join(stateDir, 'loop-detector.json'), 'utf8'));
      assert.equal(midState.read_count, 1,
        `read_count must be reset to 0 then incremented to 1 by the Read, got ${midState.read_count}`);

      // Step 2: invocation with research mode disabled — must NOT block (read_count was reset)
      stdout = '';
      const envDefault = { ...process.env, CLAUDE_PROJECT_DIR: tmpDir };
      delete envDefault.MERCURY_LOOP_DETECTOR_MODE;
      try {
        stdout = execFileSync('node', [HOOK], {
          input: JSON.stringify({
            tool_name: 'Read', tool_input: { file_path: '/y.txt' },
            tool_response: 'ok', session_id
          }),
          env: envDefault, timeout: 10000
        }).toString();
      } catch (e) {
        stdout = e.stdout ? e.stdout.toString() : '';
        if (e.status !== 0) throw e;
      }
      assert.ok(!stdout.includes('"block"'),
        `default-mode invocation after research must NOT instant-block (read_count was reset), got: ${stdout}`);
      const finalState = JSON.parse(fs.readFileSync(path.join(stateDir, 'loop-detector.json'), 'utf8'));
      assert.equal(finalState.read_count, 2,
        `read_count must continue from reset baseline (1 + 1 = 2), got ${finalState.read_count}`);
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });

  test('research mode persistence: many reads under research keep state.read_count = 1 (reset+increment)', () => {
    // Cross-session safety: even with 20 consecutive Reads under research mode,
    // persisted state.read_count must remain bounded (not accumulate forever).
    // Each invocation: main() resets to 0 → update() increments to 1 → save = 1.
    const { execFileSync } = require('child_process');
    const HOOK = path.join(__dirname, 'hook.cjs');
    const tmpDir = makeTmpDir();

    try {
      const session_id = `ete-rwt-persist-${process.pid}-${Date.now()}`;
      const stateDir = path.join(tmpDir, '.mercury', 'state');
      fs.mkdirSync(stateDir, { recursive: true });
      fs.writeFileSync(path.join(stateDir, 'loop-detector.json'), JSON.stringify({
        session_id,
        dup_count: 0, dup_tool: null, dup_hash: null,
        err_count: 0, err_last: null,
        read_count: 0, np_count: 0,
        last_activity_ts: Date.now(), last_write_ts: Date.now(),
        last_progress_ts: Date.now()
      }, null, 2));

      const envResearch = { ...process.env, CLAUDE_PROJECT_DIR: tmpDir, MERCURY_LOOP_DETECTOR_MODE: 'research' };
      // 20 consecutive Reads, each with distinct hash to avoid duplicate_call.
      // After each: read_count is reset to 0 by main(), then incremented to 1 by update().
      // So persisted value should always be 1.
      for (let i = 0; i < 20; i++) {
        let stdout = '';
        try {
          stdout = execFileSync('node', [HOOK], {
            input: JSON.stringify({
              tool_name: 'Read', tool_input: { file_path: `/path-${i}.txt` },
              tool_response: 'ok', session_id
            }),
            env: envResearch, timeout: 10000
          }).toString();
        } catch (e) {
          stdout = e.stdout ? e.stdout.toString() : '';
          if (e.status !== 0) throw e;
        }
        assert.ok(!stdout.includes('"block"'), `iteration ${i} must NOT block`);
      }

      const finalState = JSON.parse(fs.readFileSync(path.join(stateDir, 'loop-detector.json'), 'utf8'));
      assert.equal(finalState.read_count, 1,
        `under research mode, persisted read_count must remain 1 (reset before each increment), got ${finalState.read_count}`);
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });

  test('research mode does NOT bypass timeout heuristics (hard-timeout still fires)', () => {
    const { execFileSync } = require('child_process');
    const HOOK = path.join(__dirname, 'hook.cjs');
    const tmpDir = makeTmpDir();

    try {
      const session_id = `ete-rwt-research-timeout-${process.pid}-${Date.now()}`;
      const stateDir = path.join(tmpDir, '.mercury', 'state');
      fs.mkdirSync(stateDir, { recursive: true });
      const now = Date.now();
      // Old last_progress_ts (>900s) should still trigger hard-timeout even
      // under research mode. Mode only suppresses read_write_ratio, nothing else.
      fs.writeFileSync(path.join(stateDir, 'loop-detector.json'), JSON.stringify({
        session_id,
        dup_count: 0, dup_tool: null, dup_hash: null,
        err_count: 0, err_last: null,
        read_count: 0, np_count: 0,
        last_activity_ts: now - 1_000,
        last_write_ts:    now - 1_000_000,
        last_progress_ts: now - 1_000_000
      }, null, 2));

      let stdout = '';
      const env = { ...process.env, CLAUDE_PROJECT_DIR: tmpDir, MERCURY_LOOP_DETECTOR_MODE: 'research' };
      try {
        stdout = execFileSync('node', [HOOK], {
          input: JSON.stringify({
            tool_name: 'WebSearch',
            tool_input: { query: 'docs' },
            tool_response: 'ok',
            session_id
          }),
          env,
          timeout: 10000
        }).toString();
      } catch (e) {
        stdout = e.stdout ? e.stdout.toString() : '';
        if (e.status !== 0) throw e;
      }
      assert.ok(stdout.includes('"block"'),
        `research mode must NOT bypass hard-timeout, got: ${stdout}`);
      assert.ok(stdout.includes('hard timeout'),
        `block reason must be hard timeout (not read_write_ratio), got: ${stdout}`);
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });
});

// ── 17. report.cjs state_snapshot includes last_progress_ts (Issue #372) ─────

describe('writeStallReport state_snapshot.last_progress_ts', () => {
  let tmpDir;
  beforeEach(() => { tmpDir = makeTmpDir(); });
  afterEach(() => { fs.rmSync(tmpDir, { recursive: true, force: true }); });

  test('report includes last_progress_ts field', () => {
    const state = makeState({
      last_write_ts: 900, last_progress_ts: 1000, last_activity_ts: 1100
    });
    const fpath = writeStallReport(tmpDir, 'sess-progts', 'timeout_hard', 'reason', state,
      { name: 'Bash', input_hash: 'aa', errored: false, err_sig: null });
    const report = JSON.parse(fs.readFileSync(fpath, 'utf8'));
    assert.equal(report.state_snapshot.last_progress_ts, 1000,
      'state_snapshot must surface last_progress_ts for forensics');
    assert.equal(report.state_snapshot.last_write_ts, 900,
      'last_write_ts retained alongside last_progress_ts');
  });

  test('report defaults last_progress_ts to null when absent on state', () => {
    const state = makeState({ last_progress_ts: undefined });
    const fpath = writeStallReport(tmpDir, 'sess-progts-null', 'timeout_hard', 'reason', state,
      { name: 'Bash', input_hash: 'aa', errored: false, err_sig: null });
    const report = JSON.parse(fs.readFileSync(fpath, 'utf8'));
    assert.equal(report.state_snapshot.last_progress_ts, null);
  });
});

describe('Issue #546: idle-resume does not false-block; env kill-switch', () => {
  const { execFileSync } = require('child_process');
  const HOOK = path.join(__dirname, 'hook.cjs');
  let counter = 0;
  let tmpDirs = [];
  function uniqueSession() { return `ete-546-${process.pid}-${++counter}-${Date.now()}`; }
  function seedState(stateDir, session_id, over) {
    fs.mkdirSync(stateDir, { recursive: true });
    fs.writeFileSync(path.join(stateDir, 'loop-detector.json'), JSON.stringify({
      session_id, dup_count: 0, dup_tool: null, dup_hash: null, err_count: 0, err_last: null,
      read_count: 0, np_count: 0, last_activity_ts: null, last_write_ts: null, last_progress_ts: null,
      ...over
    }, null, 2));
  }
  function runHook(tmpDir, stdinObj, extraEnv) {
    let stdout = '';
    try {
      stdout = execFileSync('node', [HOOK], {
        input: JSON.stringify(stdinObj),
        env: { ...process.env, CLAUDE_PROJECT_DIR: tmpDir, ...(extraEnv || {}) },
        timeout: 10000
      }).toString();
    } catch (e) { stdout = e.stdout ? e.stdout.toString() : ''; if (e.status !== 0) throw e; }
    return stdout;
  }
  afterEach(() => {
    for (const d of tmpDirs) { try { fs.rmSync(d, { recursive: true, force: true }); } catch { /* ignore */ } }
    tmpDirs = [];
  });

  // ── unit: updateTimestamps idle-resume heal ──
  test('updateTimestamps heals last_progress_ts on idle-resume (gap > idle) even for a read', () => {
    const now = 1_700_000_000_000;
    // Previous tool call 10 min ago (600s > 300s idle); this call is a read.
    const state = makeState({ last_activity_ts: now - 600_000, last_write_ts: now - 600_000, last_progress_ts: now - 600_000 });
    updateTimestamps(state, /*is_write*/ false, /*is_progress*/ false, now, /*idleResumeSec*/ 300);
    assert.equal(state.last_progress_ts, now, 'idle-resume must reset progress clock to now');
    assert.equal(state.last_write_ts, now - 600_000, 'idle-resume must NOT touch last_write_ts (write forensics preserved)');
  });

  test('updateTimestamps does NOT heal on continuous activity (gap <= idle)', () => {
    const now = 1_700_000_000_000;
    // Previous tool call 3s ago (continuous); progress clock 800s stale (real stall building).
    const state = makeState({ last_activity_ts: now - 3_000, last_write_ts: now - 800_000, last_progress_ts: now - 800_000 });
    updateTimestamps(state, false, false, now, 300);
    assert.equal(state.last_progress_ts, now - 800_000, 'continuous read must NOT reset progress clock (stall detection preserved)');
  });

  test('updateTimestamps idle-resume gap exactly at threshold does NOT heal (strict >)', () => {
    const now = 1_700_000_000_000;
    const state = makeState({ last_activity_ts: now - 300_000, last_write_ts: now - 800_000, last_progress_ts: now - 800_000 });
    updateTimestamps(state, false, false, now, 300); // gap == 300s, not > 300
    assert.equal(state.last_progress_ts, now - 800_000, 'gap exactly at threshold is not a resume');
  });

  test('updateTimestamps omitting idleResumeSec keeps pre-#546 behaviour (no heal)', () => {
    const now = 1_700_000_000_000;
    const state = makeState({ last_activity_ts: now - 600_000, last_write_ts: now - 600_000, last_progress_ts: now - 600_000 });
    updateTimestamps(state, false, false, now); // 4-arg legacy call
    assert.equal(state.last_progress_ts, now - 600_000, 'no idleResumeSec → old behaviour, read does not bump');
  });

  test('updateTimestamps idle-resume ignores null prevActivity (cannot compute gap)', () => {
    const now = 1_700_000_000_000;
    const state = makeState({ last_activity_ts: null, last_write_ts: now - 800_000, last_progress_ts: now - 800_000 });
    updateTimestamps(state, false, false, now, 300);
    assert.equal(state.last_progress_ts, now - 800_000, 'null prevActivity → no idle-resume heal');
  });

  // ── ETE: the actual bug repro ──
  test('ETE #546: idle ~10h then Read does NOT false-block (bug repro)', () => {
    const tmpDir = makeTmpDir(); tmpDirs.push(tmpDir);
    const session_id = uniqueSession();
    const stateDir = path.join(tmpDir, '.mercury', 'state');
    const now = Date.now();
    // Session idle ~10h: nothing ran during the gap → last_activity_ts and
    // last_progress_ts both ~10h stale. Resume with Grep (read). Pre-#546 this
    // hard-blocked ("36657s since last progress"); with the fix it must pass.
    seedState(stateDir, session_id, {
      last_activity_ts: now - 36_000_000, last_write_ts: now - 36_000_000, last_progress_ts: now - 36_000_000
    });
    const stdout = runHook(tmpDir, { tool_name: 'Grep', tool_input: { pattern: 'x' }, tool_response: 'found 1', session_id });
    assert.ok(!stdout.includes('"block"'), `idle-resume Grep must NOT block, got: ${stdout}`);
    const finalState = JSON.parse(fs.readFileSync(path.join(stateDir, 'loop-detector.json'), 'utf8'));
    assert.ok(finalState.last_progress_ts >= now - 2_000, 'progress clock healed to ~now on resume');
    assert.equal(finalState.last_write_ts, now - 36_000_000, 'last_write_ts unchanged (read + idle-resume must not fake a write)');
  });

  // ── ETE: real stall still blocks ──
  test('ETE #546: continuous activity + stale progress still hard-blocks (stall detection preserved)', () => {
    const tmpDir = makeTmpDir(); tmpDirs.push(tmpDir);
    const session_id = uniqueSession();
    const stateDir = path.join(tmpDir, '.mercury', 'state');
    const now = Date.now();
    // Continuous: last tool 3s ago, but progress clock 1000s stale (> 900 hard) →
    // a genuine read-only stall. Must still block.
    seedState(stateDir, session_id, {
      last_activity_ts: now - 3_000, last_write_ts: now - 1_000_000, last_progress_ts: now - 1_000_000
    });
    const stdout = runHook(tmpDir, { tool_name: 'Grep', tool_input: { pattern: 'y' }, tool_response: 'found 1', session_id });
    assert.ok(stdout.includes('"block"'), `continuous read-only stall must still block, got: ${stdout}`);
  });

  // ── ETE: env kill-switch ──
  test('ETE #546: MERCURY_LOOP_DETECTOR_DISABLED=1 disables detector (would-block scenario passes)', () => {
    const tmpDir = makeTmpDir(); tmpDirs.push(tmpDir);
    const session_id = uniqueSession();
    const stateDir = path.join(tmpDir, '.mercury', 'state');
    const now = Date.now();
    seedState(stateDir, session_id, {
      last_activity_ts: now - 3_000, last_write_ts: now - 1_000_000, last_progress_ts: now - 1_000_000
    });
    const stdout = runHook(tmpDir,
      { tool_name: 'Grep', tool_input: { pattern: 'y' }, tool_response: 'found 1', session_id },
      { MERCURY_LOOP_DETECTOR_DISABLED: '1' });
    assert.ok(!stdout.includes('"block"'), `env kill-switch must disable blocking, got: ${stdout}`);
  });

  test('ETE #546: MERCURY_LOOP_DETECTOR_DISABLED=0 does NOT disable (would-block scenario still blocks)', () => {
    const tmpDir = makeTmpDir(); tmpDirs.push(tmpDir);
    const session_id = uniqueSession();
    const stateDir = path.join(tmpDir, '.mercury', 'state');
    const now = Date.now();
    seedState(stateDir, session_id, {
      last_activity_ts: now - 3_000, last_write_ts: now - 1_000_000, last_progress_ts: now - 1_000_000
    });
    const stdout = runHook(tmpDir,
      { tool_name: 'Grep', tool_input: { pattern: 'y' }, tool_response: 'found 1', session_id },
      { MERCURY_LOOP_DETECTOR_DISABLED: '0' });
    assert.ok(stdout.includes('"block"'), `DISABLED=0 must NOT disable — stall still blocks, got: ${stdout}`);
  });
});
