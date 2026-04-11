#!/usr/bin/env node
'use strict';

// Mercury Sliding-Window Loop Detector
// PostToolUse hook — detects stall/loop patterns in tool call sequences
//
// Design reference:
//   Ralph   — frankbria/ralph-claude-code (MIT) — circuit_breaker.sh threshold variables
//   Citadel — SethGammon/Citadel (MIT) — hooks_src/circuit-breaker.js per-project config
//   Mercury mercury-test-gate — adapters/mercury-test-gate/hook.cjs architecture pattern
//
// Note: Claude Code's PostToolUse fires for ALL tool completions (success and failure).
// Error detection reads tool_response text, which includes error output for failed tools.

const fs     = require('fs');
const path   = require('path');
const crypto = require('crypto');

const TAG   = '[mercury-loop-detector]';
const block = (reason) => { process.stdout.write(JSON.stringify({ decision: 'block', reason }) + '\n'); process.exit(0); };
const pass  = () => process.exit(0);

// ── Config ──────────────────────────────────────────────────────────────────

const DEFAULTS = {
  enabled: true,
  no_progress_threshold: 5,       // consecutive action calls (non-read/write/error) with no write
  same_error_threshold: 5,        // consecutive calls sharing the same error signature
  duplicate_call_threshold: 3,    // consecutive identical tool+input hash calls (success only)
  read_write_ratio_threshold: 8   // consecutive read-only calls with no writes
};

function clampInt(v, min, max, fallback) {
  return Number.isFinite(v) && v >= min && v <= max ? Math.round(v) : fallback;
}

function loadConfig(cwd) {
  try {
    const p = JSON.parse(fs.readFileSync(path.join(cwd, '.mercury', 'config', 'loop-detector.json'), 'utf8'));
    return {
      enabled:                  p.enabled !== false,
      no_progress_threshold:    clampInt(p.no_progress_threshold,    1, 100, DEFAULTS.no_progress_threshold),
      same_error_threshold:     clampInt(p.same_error_threshold,     1, 100, DEFAULTS.same_error_threshold),
      duplicate_call_threshold: clampInt(p.duplicate_call_threshold, 1, 100, DEFAULTS.duplicate_call_threshold),
      read_write_ratio_threshold: clampInt(p.read_write_ratio_threshold, 1, 100, DEFAULTS.read_write_ratio_threshold)
    };
  } catch {
    return Object.assign({}, DEFAULTS);
  }
}

// ── State (independent counters per session) ─────────────────────────────────

const EMPTY_STATE = () => ({ session_id: null,
  dup_count: 0, dup_tool: null, dup_hash: null, err_count: 0, err_last: null,
  read_count: 0, np_count: 0 });

function safeInt(v) { return Number.isFinite(v) && v >= 0 ? Math.round(v) : 0; }

function loadState(statePath) {
  try {
    const s = JSON.parse(fs.readFileSync(statePath, 'utf8'));
    return {
      session_id: typeof s.session_id === 'string' ? s.session_id : null,
      dup_count:  safeInt(s.dup_count),  dup_tool: typeof s.dup_tool === 'string' ? s.dup_tool : null,
      dup_hash:   typeof s.dup_hash === 'string' ? s.dup_hash : null,
      err_count:  safeInt(s.err_count),  err_last: typeof s.err_last === 'string' ? s.err_last : null,
      read_count: safeInt(s.read_count),
      np_count:   safeInt(s.np_count)
    };
  } catch {
    return EMPTY_STATE();
  }
}

// Atomic write via unique temp-file + rename (unique name prevents concurrent-write collisions).
function saveState(statePath, state) {
  fs.mkdirSync(path.dirname(statePath), { recursive: true });
  const tmp = `${statePath}.${process.pid}.${Date.now()}.${Math.random().toString(16).slice(2)}.tmp`;
  try {
    fs.writeFileSync(tmp, JSON.stringify(state, null, 2));
    fs.renameSync(tmp, statePath);
  } finally {
    try { if (fs.existsSync(tmp)) fs.unlinkSync(tmp); } catch { /* ignore cleanup error */ }
  }
}

// ── Tool classification ──────────────────────────────────────────────────────

const WRITE_TOOLS = new Set(['Edit', 'Write', 'NotebookEdit', 'MultiEdit']);
const READ_TOOLS  = new Set(['Read', 'Glob', 'Grep']);

// ── Helpers ──────────────────────────────────────────────────────────────────
function hashInput(input) {
  const s = typeof input === 'string' ? input : JSON.stringify(input ?? '');
  return crypto.createHash('sha256').update(s).digest('hex').slice(0, 8); // sha256: FIPS-safe
}

function toStr(v) { return typeof v === 'string' ? v : JSON.stringify(v ?? ''); }

// Scan first+last 1500 chars only; skips incidental keyword matches in large JSON blobs.
function scanStr(r) { const s = toStr(r); return s.length <= 3000 ? s : s.slice(0, 1500) + '\n' + s.slice(-1500); }

function hasError(response) {
  return /\b(?:error|failed|exception)\b|exit code [1-9]/i.test(scanStr(response));
}

function errorSig(response) {
  const m = scanStr(response).match(/(?:error|failed|exception|exit code \d+)[^.\n]{0,80}/i);
  return m ? m[0].trim().slice(0, 80) : null;
}
// ── Update independent counters ──────────────────────────────────────────────

function update(state, tool, hash, is_write, is_read, errored, err_sig) {
  if (!errored) {
    if (tool === state.dup_tool && hash === state.dup_hash) { state.dup_count++; }
    else { state.dup_count = 1; state.dup_tool = tool; state.dup_hash = hash; }
  } else {
    state.dup_count = 0; state.dup_tool = null; state.dup_hash = null;
  }

  if (errored && err_sig) {
    if (err_sig === state.err_last) { state.err_count++; }
    else { state.err_count = 1; state.err_last = err_sig; }
  } else {
    state.err_count = 0; state.err_last = null;
  }

  if (is_write)     { state.read_count = 0; }
  else if (is_read) { state.read_count++; }
  else              { state.read_count = 0; }

  if (is_write || is_read || errored) { state.np_count = 0; }
  else                                { state.np_count++; }
}

// ── Detect stall (most-specific signal first) ────────────────────────────────

function detectStall(state, cfg) {
  if (state.dup_count  >= cfg.duplicate_call_threshold)
    return `duplicate_call: ${state.dup_count} identical ${state.dup_tool} calls (hash:${state.dup_hash})`;
  if (state.err_count  >= cfg.same_error_threshold)
    return `same_error: ${state.err_count} identical errors — "${state.err_last}"`;
  if (state.read_count >= cfg.read_write_ratio_threshold)
    return `read_write_ratio: ${state.read_count} consecutive read-only calls with no writes`;
  if (state.np_count   >= cfg.no_progress_threshold)
    return `no_progress: ${state.np_count} consecutive action calls with no file write`;
  return null;
}

// ── Main ─────────────────────────────────────────────────────────────────────

function main() {
  let input;
  try { input = JSON.parse(fs.readFileSync(0, 'utf8')); }
  catch (e) { process.stderr.write(`${TAG} WARNING: stdin parse failed (${e.message}); fail-open\n`); pass(); }

  const { tool_name = '', tool_input = {}, tool_response = '', session_id = '' } = input;
  const cwd = process.env.CLAUDE_PROJECT_DIR || process.cwd();

  // Fail-open when session_id is absent: without it we cannot isolate per-session state
  // and would risk cross-session counter contamination.
  if (!session_id) {
    process.stderr.write(`${TAG} WARNING: no session_id in hook payload; skipping state accumulation\n`);
    pass();
  }

  const cfg = loadConfig(cwd);
  if (!cfg.enabled) pass();

  const statePath = path.join(cwd, '.mercury', 'state', 'loop-detector.json');
  const state     = loadState(statePath);

  // Reset all counters when session changes
  if (state.session_id !== session_id) {
    const fresh = EMPTY_STATE();
    fresh.session_id = session_id;
    Object.assign(state, fresh);
  }

  const errored  = hasError(tool_response);
  const err_sig  = errored ? errorSig(tool_response) : null;
  const is_write = WRITE_TOOLS.has(tool_name);
  const is_read  = READ_TOOLS.has(tool_name);

  update(state, tool_name, hashInput(tool_input), is_write, is_read, errored, err_sig);
  saveState(statePath, state);

  const stall = detectStall(state, cfg);
  if (stall) {
    Object.assign(state, EMPTY_STATE()); // reset all counters after firing
    state.session_id = session_id;
    saveState(statePath, state);
    block(`Mercury loop detector: ${stall}\n(Buffer reset. If this is a false positive, resume your work.)`);
  }

  pass();
}

try { main(); } catch (e) { process.stderr.write(`${TAG} fatal: ${e.message}\n`); process.exit(0); }
