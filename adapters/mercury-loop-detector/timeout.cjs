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
 * Called after update() so is_write reflects the current tool call.
 * @param {object} state - mutable state object
 * @param {boolean} is_write
 * @param {number} now - Date.now() ms
 */
function updateTimestamps(state, is_write, now) {
  state.last_activity_ts = now;
  if (is_write) {
    state.last_write_ts = now;
  }
  // Initialise last_write_ts on first call (no prior write seen this session)
  if (!Number.isFinite(state.last_write_ts)) {
    state.last_write_ts = now;
  }
}

/**
 * Check multi-level timeout based on elapsed time since last write.
 * Purely retrospective — evaluated at PostToolUse fire time.
 *
 * @param {object} state - current state (must have last_write_ts)
 * @param {object} cfg   - config from loadConfig()
 * @param {number} now   - Date.now() ms
 * @returns {null | { level: 'soft'|'idle'|'hard', message: string, should_block: boolean }}
 */
function checkMultiLevel(state, cfg, now) {
  const lastWrite = state.last_write_ts;
  if (!Number.isFinite(lastWrite)) return null;

  const elapsed = Math.floor((now - lastWrite) / 1000); // seconds
  if (elapsed < 0) return null;

  const { soft, idle, hard } = resolveThresholds(cfg);

  if (elapsed > hard) {
    const msg = `${TAG} WARNING: hard timeout: ${elapsed}s since last write (threshold: ${hard}s) — blocking`;
    return { level: 'hard', message: msg, should_block: true };
  }
  if (elapsed > idle) {
    const msg = `${TAG} WARNING: idle timeout: ${elapsed}s since last write (threshold: ${idle}s) — consider /handoff or resume with a write`;
    return { level: 'idle', message: msg, should_block: false };
  }
  if (elapsed > soft) {
    const msg = `${TAG} WARNING: soft timeout: ${elapsed}s since last write (threshold: ${soft}s)`;
    return { level: 'soft', message: msg, should_block: false };
  }

  return null;
}

module.exports = { checkMultiLevel, updateTimestamps, resolveThresholds, TIMEOUT_DEFAULTS };
