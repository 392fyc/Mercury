'use strict';
const fs = require('fs');
const path = require('path');
const MAX_BLOCKS = 3;

// Advisory lockfile (exclusive-create + spin-wait) serializes concurrent R-M-W.
// Atomic rename prevents partial writes; lockfile prevents lost-update races.
function _withLock(lockfile, fn) {
  const deadline = Date.now() + 2000;
  while (true) {
    let fd;
    try { fd = fs.openSync(lockfile, 'wx'); }
    catch (e) {
      if (e.code !== 'EEXIST') throw e;
      if (Date.now() > deadline) throw new Error('attempt-tracker lock timeout');
      const end = Date.now() + 15; while (Date.now() < end) { /* spin */ } continue;
    }
    try { return fn(); } finally { try { fs.closeSync(fd); fs.unlinkSync(lockfile); } catch (_) {} }
  }
}

// Returns { shouldBlock, count }
function checkAndIncrement(stateDir, sessionId, agentId) {
  const file = path.join(stateDir, 'test-gate-attempts.json');
  fs.mkdirSync(stateDir, { recursive: true });
  return _withLock(path.join(stateDir, 'test-gate-attempts.lock'), () => {
    let state = {};
    try { state = JSON.parse(fs.readFileSync(file, 'utf8')); } catch (_) {}
    const key = `${sessionId}:${agentId}`;
    const entry = state[key] || { count: 0, first_attempt_at: new Date().toISOString() };
    if (entry.count >= MAX_BLOCKS) {
      delete state[key]; _save(file, state);
      return { shouldBlock: false, count: entry.count };
    }
    entry.count++;
    if (entry.count === 1) entry.first_attempt_at = new Date().toISOString();
    state[key] = entry; _save(file, state);
    return { shouldBlock: true, count: entry.count };
  });
}

// Called from hook.cjs on tests-pass to clear stale counters (prevents unbounded accumulation at 1/2).
function clearAttempts(stateDir, sessionId, agentId) {
  const file = path.join(stateDir, 'test-gate-attempts.json');
  if (!fs.existsSync(file)) return;
  fs.mkdirSync(stateDir, { recursive: true });
  _withLock(path.join(stateDir, 'test-gate-attempts.lock'), () => {
    let state = {};
    try { state = JSON.parse(fs.readFileSync(file, 'utf8')); } catch (_) { return; }
    const key = `${sessionId}:${agentId}`;
    if (state[key]) { delete state[key]; _save(file, state); }
  });
}

function _save(file, state) {
  const tmp = `${file}.${process.pid}.${Date.now()}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2));
  fs.renameSync(tmp, file);  // atomic on POSIX + Windows
}

module.exports = { checkAndIncrement, clearAttempts, MAX_BLOCKS };
