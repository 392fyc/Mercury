#!/usr/bin/env node
'use strict';

// Mercury Sliding-Window Loop Detector — PostToolUse hook
// Detects stall/loop patterns; coordinates timeout.cjs + report.cjs modules.
// Design ref: Ralph (frankbria/ralph-claude-code MIT), Citadel (SethGammon MIT),
//             mercury-test-gate adapter pattern.

const fs     = require('fs');
const path   = require('path');
const crypto = require('crypto');
const { checkMultiLevel, updateTimestamps } = require('./timeout.cjs');
const { writeStallReport }                  = require('./report.cjs');

const TAG   = '[mercury-loop-detector]';
const block = (r) => { process.stdout.write(JSON.stringify({ decision: 'block', reason: r }) + '\n'); process.exit(0); };
const pass  = () => process.exit(0);

// ── Config ───────────────────────────────────────────────────────────────────
const DEFAULTS = { enabled: true, no_progress_threshold: 5, same_error_threshold: 5,
  duplicate_call_threshold: 3, read_write_ratio_threshold: 8 };

function clampInt(v, min, max, fb) { return Number.isFinite(v) && v >= min && v <= max ? Math.round(v) : fb; }

function loadConfig(cwd) {
  try {
    const p = JSON.parse(fs.readFileSync(path.join(cwd, '.mercury', 'config', 'loop-detector.json'), 'utf8'));
    return {
      enabled:                    p.enabled !== false,
      no_progress_threshold:      clampInt(p.no_progress_threshold,      1, 100, DEFAULTS.no_progress_threshold),
      same_error_threshold:       clampInt(p.same_error_threshold,       1, 100, DEFAULTS.same_error_threshold),
      duplicate_call_threshold:   clampInt(p.duplicate_call_threshold,   1, 100, DEFAULTS.duplicate_call_threshold),
      read_write_ratio_threshold: clampInt(p.read_write_ratio_threshold, 1, 100, DEFAULTS.read_write_ratio_threshold),
      timeout_soft_sec: clampInt(p.timeout_soft_sec, 1, 3600, undefined),
      timeout_idle_sec: clampInt(p.timeout_idle_sec, 1, 3600, undefined),
      timeout_hard_sec: clampInt(p.timeout_hard_sec, 1, 3600, undefined)
    };
  } catch { return Object.assign({}, DEFAULTS); }
}

// ── State ────────────────────────────────────────────────────────────────────
const EMPTY_STATE = () => ({ session_id: null,
  dup_count: 0, dup_tool: null, dup_hash: null, err_count: 0, err_last: null,
  read_count: 0, np_count: 0, last_activity_ts: null, last_write_ts: null });

function safeInt(v) { return Number.isFinite(v) && v >= 0 ? Math.round(v) : 0; }

function loadState(statePath) {
  try {
    const s = JSON.parse(fs.readFileSync(statePath, 'utf8'));
    return {
      session_id:       typeof s.session_id === 'string' ? s.session_id : null,
      dup_count:        safeInt(s.dup_count),
      dup_tool:         typeof s.dup_tool  === 'string' ? s.dup_tool  : null,
      dup_hash:         typeof s.dup_hash  === 'string' ? s.dup_hash  : null,
      err_count:        safeInt(s.err_count),
      err_last:         typeof s.err_last  === 'string' ? s.err_last  : null,
      read_count:       safeInt(s.read_count), np_count: safeInt(s.np_count),
      last_activity_ts: Number.isFinite(s.last_activity_ts) ? s.last_activity_ts : null,
      last_write_ts:    Number.isFinite(s.last_write_ts)    ? s.last_write_ts    : null
    };
  } catch { return EMPTY_STATE(); }
}

function saveState(statePath, state) {
  fs.mkdirSync(path.dirname(statePath), { recursive: true });
  const tmp = `${statePath}.${process.pid}.${Date.now()}.${Math.random().toString(16).slice(2)}.tmp`;
  try { fs.writeFileSync(tmp, JSON.stringify(state, null, 2)); fs.renameSync(tmp, statePath); }
  finally { try { if (fs.existsSync(tmp)) fs.unlinkSync(tmp); } catch { /* ignore */ } }
}

// ── Tool classification + helpers ────────────────────────────────────────────
const WRITE_TOOLS = new Set(['Edit', 'Write', 'NotebookEdit', 'MultiEdit']);
const READ_TOOLS  = new Set(['Read', 'Glob', 'Grep']);

function hashInput(input) {
  const s = typeof input === 'string' ? input : JSON.stringify(input ?? '');
  return crypto.createHash('sha256').update(s).digest('hex').slice(0, 8);
}
function toStr(v)    { return typeof v === 'string' ? v : JSON.stringify(v ?? ''); }
function scanStr(r)  { const s = toStr(r); return s.length <= 3000 ? s : s.slice(0, 1500) + '\n' + s.slice(-1500); }
function hasError(r) { return /\b(?:error|failed|exception)\b|exit code [1-9]/i.test(scanStr(r)); }
function errorSig(r) {
  const m = scanStr(r).match(/(?:error|failed|exception|exit code \d+)[^.\n]{0,80}/i);
  return m ? m[0].trim().slice(0, 80) : null;
}

// ── Update independent counters ───────────────────────────────────────────────
function update(state, tool, hash, is_write, is_read, errored, err_sig) {
  if (!errored) {
    if (tool === state.dup_tool && hash === state.dup_hash) { state.dup_count++; }
    else { state.dup_count = 1; state.dup_tool = tool; state.dup_hash = hash; }
  } else { state.dup_count = 0; state.dup_tool = null; state.dup_hash = null; }
  if (errored && err_sig) {
    if (err_sig === state.err_last) { state.err_count++; }
    else { state.err_count = 1; state.err_last = err_sig; }
  } else { state.err_count = 0; state.err_last = null; }
  if (is_write)     { state.read_count = 0; }
  else if (is_read) { state.read_count++; }
  else              { state.read_count = 0; }
  if (is_write || is_read || errored) { state.np_count = 0; } else { state.np_count++; }
}

// ── Detect stall (most-specific signal first) ─────────────────────────────────
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

// ── Main ──────────────────────────────────────────────────────────────────────
function main() {
  let input;
  try { input = JSON.parse(fs.readFileSync(0, 'utf8')); }
  catch (e) { process.stderr.write(`${TAG} WARNING: stdin parse failed (${e.message}); fail-open\n`); pass(); }

  const { tool_name = '', tool_input = {}, tool_response = '', session_id = '' } = input;
  const cwd = process.env.CLAUDE_PROJECT_DIR || process.cwd();

  if (!session_id) { process.stderr.write(`${TAG} WARNING: no session_id; skipping state accumulation\n`); pass(); }

  const cfg = loadConfig(cwd);
  if (!cfg.enabled) pass();

  const statePath = path.join(cwd, '.mercury', 'state', 'loop-detector.json');
  const state     = loadState(statePath);
  if (state.session_id !== session_id) { Object.assign(state, EMPTY_STATE()); state.session_id = session_id; }

  const errored  = hasError(tool_response);
  const err_sig  = errored ? errorSig(tool_response) : null;
  const is_write = WRITE_TOOLS.has(tool_name);
  const is_read  = READ_TOOLS.has(tool_name);
  const now      = Date.now();
  const ihash    = hashInput(tool_input);

  update(state, tool_name, ihash, is_write, is_read, errored, err_sig);
  updateTimestamps(state, is_write, now);
  saveState(statePath, state);

  // Timeout check (retrospective — evaluated at PostToolUse fire time)
  const tr = checkMultiLevel(state, cfg, now);
  if (tr) {
    process.stderr.write(tr.message + '\n');
    if (tr.should_block) {
      const fullReason = `Mercury loop detector: ${tr.message}\n(Buffer reset. If this is a false positive, resume your work.)`;
      writeStallReport(cwd, session_id, 'timeout_hard', fullReason, state,
        { name: tool_name, input_hash: ihash, errored, err_sig });
      try { const { notify } = require('../mercury-notify/notify.cjs'); notify('error', `Mercury stall: timeout_hard`, tr.message).catch(() => {}); } catch { /* notify load failure non-fatal */ }
      Object.assign(state, EMPTY_STATE()); state.session_id = session_id; saveState(statePath, state);
      block(fullReason);
    }
  }

  // Stall signal check
  const stall = detectStall(state, cfg);
  if (stall) {
    const fullReason = `Mercury loop detector: ${stall.reason}\n(Buffer reset. If this is a false positive, resume your work.)`;
    writeStallReport(cwd, session_id, stall.type, fullReason, state,
      { name: tool_name, input_hash: ihash, errored, err_sig });
    try { const { notify } = require('../mercury-notify/notify.cjs'); notify('error', `Mercury stall: ${stall.type}`, stall.reason).catch(() => {}); } catch { /* notify load failure non-fatal */ }
    Object.assign(state, EMPTY_STATE()); state.session_id = session_id; saveState(statePath, state);
    block(fullReason);
  }

  pass();
}

try { main(); } catch (e) { process.stderr.write(`${TAG} fatal: ${e.message}\n`); process.exit(0); }
