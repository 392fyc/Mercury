'use strict';
const fs = require('fs');
const path = require('path');
const MAX_BLOCKS = 3;

// State: { "<session_id>:<agent_id>": { count, first_attempt_at } }
// Returns { shouldBlock, count }
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

function _save(file, state) {
  try { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, JSON.stringify(state, null, 2)); } catch (_) {}
}

module.exports = { checkAndIncrement, MAX_BLOCKS };
