'use strict';
const fs = require('fs');
const path = require('path');
const MAX_BLOCKS = 3;

// State: { "<session_id>:<agent_id>": { count, first_attempt_at } }
// Concurrency: read-modify-write uses atomic rename (write to unique temp, then rename).
// Atomic rename is cross-platform (POSIX + Windows) and survives concurrent writers
// from parallel dev-agent sessions without clobbering each other.
function checkAndIncrement(stateDir, sessionId, agentId) {
  const file = path.join(stateDir, 'test-gate-attempts.json');
  let state = {};
  try { state = JSON.parse(fs.readFileSync(file, 'utf8')); } catch (_) {}
  const key = `${sessionId}:${agentId}`;
  const entry = state[key] || { count: 0, first_attempt_at: new Date().toISOString() };
  if (entry.count >= MAX_BLOCKS) {
    delete state[key];
    _save(file, state);
    return { shouldBlock: false, count: entry.count };
  }
  entry.count++;
  if (entry.count === 1) entry.first_attempt_at = new Date().toISOString();
  state[key] = entry;
  _save(file, state);
  return { shouldBlock: true, count: entry.count };
}

// Called from hook.cjs on the tests-pass path so successful runs clear
// stale retry counters (prevents unbounded accumulation at counts 1/2).
function clearAttempts(stateDir, sessionId, agentId) {
  const file = path.join(stateDir, 'test-gate-attempts.json');
  let state = {};
  try { state = JSON.parse(fs.readFileSync(file, 'utf8')); } catch (_) { return; }
  const key = `${sessionId}:${agentId}`;
  if (state[key]) { delete state[key]; _save(file, state); }
}

function _save(file, state) {
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const tmp = `${file}.${process.pid}.${Date.now()}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(state, null, 2));
    fs.renameSync(tmp, file);  // atomic on POSIX + Windows
  } catch (_) {}
}

module.exports = { checkAndIncrement, clearAttempts, MAX_BLOCKS };
