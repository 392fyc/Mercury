#!/usr/bin/env node
'use strict';

// Mercury Loop Detector — Multi-level timeout state machine
// Reads last_write_ts from state; returns null or { level, message, should_block }
// No side effects: caller (hook.cjs) owns stderr writes and state persistence.

const TAG = '[mercury-loop-detector]';

const TIMEOUT_DEFAULTS = {
  timeout_soft_sec:  60,
  timeout_idle_sec:  300,
  timeout_hard_sec:  900
};

function clampSec(v, fallback) {
  return Number.isFinite(v) && v >= 1 && v <= 3600 ? Math.round(v) : fallback;
}

/**
 * Resolve timeout thresholds.
 * Priority: env var > config file fields > defaults.
 * @param {object} cfg - merged config object from loadConfig()
 * @returns {{ soft: number, idle: number, hard: number }} seconds
 */
function resolveThresholds(cfg) {
  const envSoft = parseFloat(process.env.MERCURY_TIMEOUT_SOFT_SEC);
  const envIdle = parseFloat(process.env.MERCURY_TIMEOUT_IDLE_SEC);
  const envHard = parseFloat(process.env.MERCURY_TIMEOUT_HARD_SEC);

  const result = {
    soft: clampSec(Number.isFinite(envSoft) ? envSoft : cfg.timeout_soft_sec,  TIMEOUT_DEFAULTS.timeout_soft_sec),
    idle: clampSec(Number.isFinite(envIdle) ? envIdle : cfg.timeout_idle_sec,  TIMEOUT_DEFAULTS.timeout_idle_sec),
    hard: clampSec(Number.isFinite(envHard) ? envHard : cfg.timeout_hard_sec,  TIMEOUT_DEFAULTS.timeout_hard_sec)
  };

  // Sanity: must satisfy soft <= idle <= hard. If not, fail-open to defaults + warn.
  if (result.soft > result.idle || result.idle > result.hard) {
    process.stderr.write(`${TAG} WARNING: timeout thresholds violate soft<=idle<=hard (got soft=${result.soft} idle=${result.idle} hard=${result.hard}); falling back to defaults\n`);
    return {
      soft: TIMEOUT_DEFAULTS.timeout_soft_sec,
      idle: TIMEOUT_DEFAULTS.timeout_idle_sec,
      hard: TIMEOUT_DEFAULTS.timeout_hard_sec
    };
  }
  return result;
}

/**
 * Update timestamp fields on state.
 * Called after update() so flags reflect the current tool call.
 *
 * Sister-fix to Issue #325 (PROGRESS_TOOLS reset np_count): timeout uses
 * last_progress_ts (write OR PROGRESS_TOOLS) instead of last_write_ts so
 * legitimate long Bash/Skill/Agent phases (PR poll, smoke, review iter)
 * do not trip hard-timeout. last_write_ts retained for forensics.
 *
 * @param {object}  state       - mutable state object
 * @param {boolean} is_write
 * @param {boolean} is_progress - PROGRESS_TOOLS membership (Bash, Agent, Skill, Task variants, ToolSearch)
 * @param {number}  now         - Date.now() ms
 */
function isPositiveTs(v) {
  return Number.isFinite(v) && v > 0;
}

// Defense-in-depth (Argus #373 iter-1 Medium 7/10): reject future-dated timestamps
// (clock skew / state pollution / test fixture mistakes). Without this, a polluted
// last_progress_ts > now would yield negative elapsed → silent permanent bypass.
// Tolerate small forward skew (e.g. Date.now() race within the same hook fire) via
// a 60-second grace period — anything beyond that is treated as invalid.
const FUTURE_TS_GRACE_MS = 60_000;
function isValidPastTs(v, now) {
  return isPositiveTs(v) && v <= now + FUTURE_TS_GRACE_MS;
}

function updateTimestamps(state, is_write, is_progress, now) {
  state.last_activity_ts = now;
  if (is_write) {
    state.last_write_ts = now;
  }
  if (is_write || is_progress) {
    state.last_progress_ts = now;
  }
  // Initialise last_write_ts on first call (no prior write seen this session).
  // Reject 0/negative timestamps as polluted state per Issue #372 (sister to #325).
  if (!isPositiveTs(state.last_write_ts)) {
    state.last_write_ts = now;
  }
  // Initialise last_progress_ts (backward-compat: old state files lack the field).
  if (!isPositiveTs(state.last_progress_ts)) {
    state.last_progress_ts = state.last_write_ts;
  }
}

/**
 * Check multi-level timeout based on elapsed time since last progress signal
 * (write OR PROGRESS_TOOLS call). Backward-compat: falls back to last_write_ts
 * when last_progress_ts is absent (old state file from pre-#372).
 * Purely retrospective — evaluated at PostToolUse fire time.
 *
 * @param {object} state - current state (must have last_progress_ts or last_write_ts)
 * @param {object} cfg   - config from loadConfig()
 * @param {number} now   - Date.now() ms
 * @returns {null | { level: 'soft'|'idle'|'hard', message: string, should_block: boolean }}
 */
function checkMultiLevel(state, cfg, now) {
  // Reject 0/negative AND future-dated ts (polluted state, clock skew) —
  // fall through fallback chain. Without the future-ts guard a polluted
  // last_progress_ts > now would yield negative elapsed → silent permanent
  // bypass (Argus #373 iter-1 Medium 7/10).
  const ref = isValidPastTs(state.last_progress_ts, now) ? state.last_progress_ts
            : isValidPastTs(state.last_write_ts, now)    ? state.last_write_ts
            : null;
  if (ref === null) return null;

  const elapsed = Math.floor((now - ref) / 1000); // seconds
  if (elapsed < 0) return null;

  const { soft, idle, hard } = resolveThresholds(cfg);

  if (elapsed > hard) {
    const msg = `${TAG} WARNING: hard timeout: ${elapsed}s since last progress (threshold: ${hard}s) — blocking`;
    return { level: 'hard', message: msg, should_block: true };
  }
  if (elapsed > idle) {
    const msg = `${TAG} WARNING: idle timeout: ${elapsed}s since last progress (threshold: ${idle}s) — consider /handoff or resume with a write`;
    return { level: 'idle', message: msg, should_block: false };
  }
  if (elapsed > soft) {
    const msg = `${TAG} WARNING: soft timeout: ${elapsed}s since last progress (threshold: ${soft}s)`;
    return { level: 'soft', message: msg, should_block: false };
  }

  return null;
}

module.exports = { checkMultiLevel, updateTimestamps, resolveThresholds, TIMEOUT_DEFAULTS };
