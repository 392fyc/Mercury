'use strict';

// Issue #298: strict env-flag parsing for MERCURY_NOTIFY_DISABLED (and any future
// boolean env flag in this adapter). Accepts '1'/'true'/'yes'/'on' (case-insensitive,
// surrounding whitespace trimmed). Returns false for unset, empty, '0', 'false', etc.
// Loose `if (process.env.X)` checks were truthy for any non-empty string including
// the literal '0', diverging from the strict `=== '1'` pattern used elsewhere in
// Mercury (mercury-loop-detector, mercury-test-gate).
//
// Sibling of adapters/mercury-notify/lib/env.cjs — kept in-tree per CLAUDE.md
// "modular design" (every adapter independently detachable; no cross-adapter
// require()). Both copies must stay in sync.
const TRUTHY = new Set(['1', 'true', 'yes', 'on']);
function isEnvTruthy(v) {
  if (v == null) return false;
  return TRUTHY.has(String(v).trim().toLowerCase());
}

module.exports = { isEnvTruthy };
