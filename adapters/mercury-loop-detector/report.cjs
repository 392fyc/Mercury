#!/usr/bin/env node
'use strict';

// Mercury Loop Detector — Stall diagnostic report serializer + pruning
// Exports writeStallReport() and pruneReports().
// Feature flag: MERCURY_STALL_REPORT_DISABLED=1 skips all writes.

const fs   = require('fs');
const path = require('path');

const TAG = '[mercury-loop-detector]';
const KEEP_DEFAULT = 50;

/**
 * Format a Date as filesystem-safe ISO string: 2026-04-24T120000123Z
 * (no colons, no dots — milliseconds retained to avoid same-second filename collision on Windows)
 * @param {Date} d
 * @returns {string}
 */
function isoFsSafe(d) {
  return d.toISOString()
    .replace(/[:.]/g, '');  // 2026-04-24T120000123Z (retain ms, remove colons+dots)
}

/**
 * Write a stall diagnostic report JSON file.
 *
 * @param {string} cwd          - project root (CLAUDE_PROJECT_DIR)
 * @param {string} session_id   - current session id from hook payload
 * @param {string} stall_type   - one of: no_progress|same_error|duplicate_call|read_write_ratio|timeout_hard
 * @param {string} stall_reason - full reason string passed to block()
 * @param {object} state        - current state snapshot (before reset)
 * @param {object} last_tool    - { name, input_hash, errored, err_sig }
 * @returns {string|null}       - written file path, or null on skip/error
 */
function writeStallReport(cwd, session_id, stall_type, stall_reason, state, last_tool) {
  if (process.env.MERCURY_STALL_REPORT_DISABLED === '1') return null;

  // Fail-open: missing or empty session_id → skip report
  if (!session_id || typeof session_id !== 'string' || session_id.trim() === '') {
    process.stderr.write(`${TAG} WARNING: cannot write stall report — empty session_id\n`);
    return null;
  }

  // Sanitize session_id for filesystem use:
  //   1. Replace non-whitelisted chars [^a-zA-Z0-9._-] with '_'
  //   2. Strip leading dots/hyphens to prevent ".." prefix and hidden-file names
  //   3. Require at least one alphanumeric char to ensure meaningful output
  //   4. Truncate to 64 chars
  // Prevents path traversal (/, ../) and illegal filename chars.
  const safeSessionId = String(session_id)
    .replace(/[^a-zA-Z0-9._-]/g, '_')
    .replace(/^[._-]+/, '')
    .slice(0, 64);
  if (!safeSessionId || !/[a-zA-Z0-9]/.test(safeSessionId)) {
    process.stderr.write(`${TAG} WARNING: cannot write stall report — session_id has no safe chars\n`);
    return null;
  }

  const now    = new Date();
  const isoTs  = now.toISOString();
  const fsTs   = isoFsSafe(now);
  const dir    = path.join(cwd, '.mercury', 'state', 'stall-reports');
  const fname  = `${safeSessionId}-${fsTs}.json`;
  const fpath  = path.join(dir, fname);

  const report = {
    timestamp:    isoTs,
    session_id:   session_id,
    stall_type:   stall_type,
    stall_reason: stall_reason,
    state_snapshot: {
      dup_count:        state.dup_count        ?? 0,
      dup_tool:         state.dup_tool         ?? null,
      dup_hash:         state.dup_hash         ?? null,
      err_count:        state.err_count        ?? 0,
      err_last:         state.err_last         ?? null,
      read_count:       state.read_count       ?? 0,
      np_count:         state.np_count         ?? 0,
      last_activity_ts: state.last_activity_ts ?? null,
      last_write_ts:    state.last_write_ts    ?? null
    },
    last_tool: {
      name:       last_tool.name       ?? null,
      input_hash: last_tool.input_hash ?? null,
      errored:    last_tool.errored    ?? false,
      err_sig:    last_tool.err_sig    ?? null
    }
  };

  const tmp = `${fpath}.${process.pid}.${Date.now()}.tmp`;
  try {
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(tmp, JSON.stringify(report, null, 2), { mode: 0o600 });
    fs.renameSync(tmp, fpath);
    pruneReports(cwd, KEEP_DEFAULT);
    return fpath;
  } catch (e) {
    process.stderr.write(`${TAG} WARNING: failed to write stall report: ${e.message}\n`);
    return null;
  } finally {
    try { if (fs.existsSync(tmp)) fs.unlinkSync(tmp); } catch { /* ignore */ }
  }
}

/**
 * Prune stall report directory: keep the newest `keep` files, unlink older ones.
 * @param {string} cwd  - project root
 * @param {number} keep - number of files to retain (default 50)
 */
function pruneReports(cwd, keep = KEEP_DEFAULT) {
  const dir = path.join(cwd, '.mercury', 'state', 'stall-reports');
  try {
    const entries = fs.readdirSync(dir)
      .filter(f => f.endsWith('.json'))
      .map(f => {
        const fp = path.join(dir, f);
        const mtime = fs.statSync(fp).mtimeMs;
        return { fp, mtime };
      })
      .sort((a, b) => b.mtime - a.mtime); // newest first

    const toDelete = entries.slice(keep);
    for (const { fp } of toDelete) {
      try { fs.unlinkSync(fp); } catch { /* ignore individual unlink errors */ }
    }
  } catch { /* dir missing or unreadable — ignore */ }
}

module.exports = { writeStallReport, pruneReports, isoFsSafe };
