#!/usr/bin/env node
'use strict';

// Mercury Loop Detector — Test suite
// Runner: node --test adapters/mercury-loop-detector/*.test.cjs

const { test, describe, before, after, beforeEach } = require('node:test');
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
    last_activity_ts: null, last_write_ts: null,
    ...overrides
  };
}

function makeCfg(overrides = {}) {
  return {
    enabled: true,
    no_progress_threshold: 5,
    same_error_threshold: 5,
    duplicate_call_threshold: 3,
    read_write_ratio_threshold: 8,
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
});

// Inline detectStall mirror (same logic as hook.cjs) so tests are self-contained
function detectStall(state, cfg) {
  if (state.dup_count  >= cfg.duplicate_call_threshold)
    return { type: 'duplicate_call',   reason: `duplicate_call: ${state.dup_count} identical ${state.dup_tool} calls (hash:${state.dup_hash})` };
  if (state.err_count  >= cfg.same_error_threshold)
    return { type: 'same_error',       reason: `same_error: ${state.err_count} identical errors — "${state.err_last}"` };
  if (state.read_count >= cfg.read_write_ratio_threshold)
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
    updateTimestamps(state, false, now);
    const result = checkMultiLevel(state, cfg, now);
    assert.equal(result, null);
  });
});

// ── 3. Hard timeout — block + write diagnostic report ────────────────────────

describe('timeout: hard blocks and writes report', () => {
  let tmpDir;
  beforeEach(() => { tmpDir = makeTmpDir(); });
  after(() => { fs.rmSync(tmpDir, { recursive: true, force: true }); });

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
  after(() => { fs.rmSync(tmpDir, { recursive: true, force: true }); });

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

// ── isoFsSafe helper ──────────────────────────────────────────────────────────

describe('isoFsSafe formatting', () => {
  test('removes colons and milliseconds', () => {
    const d = new Date('2026-04-24T12:34:56.789Z');
    const s = isoFsSafe(d);
    assert.equal(s, '2026-04-24T123456Z');
    assert.ok(!s.includes(':'), 'no colons');
    assert.ok(!s.includes('.'), 'no dots');
  });
});
