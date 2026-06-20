'use strict';
// hook.test.cjs — Unit tests for mercury-test-gate hook.cjs
// Uses node:test (built-in, no external deps)
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { execSync, spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

const HOOK = path.resolve(__dirname, '..', 'hook.cjs');

/**
 * Invoke hook.cjs with a given stdin JSON and optional env overrides.
 * Returns { stdout, stderr, status }
 */
function invokeHook(inputObj, envOverrides = {}) {
  const stdin = JSON.stringify(inputObj);
  const result = spawnSync(process.execPath, [HOOK], {
    input: stdin,
    env: { ...process.env, ...envOverrides },
    encoding: 'utf8',
    timeout: 15000,
  });
  return {
    stdout: result.stdout || '',
    stderr: result.stderr || '',
    status: result.status,
  };
}

/**
 * Create a temp dir with optional files. Returns the dir path.
 */
function makeTmpDir(files = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mtg-test-'));
  for (const [rel, content] of Object.entries(files)) {
    const full = path.join(dir, rel);
    fs.mkdirSync(path.dirname(full), { recursive: true });
    fs.writeFileSync(full, content, 'utf8');
  }
  return dir;
}

// ─── Test 1: non-dev agent → no-op exit 0 ────────────────────────────────────
test('non-dev agent type exits 0 with no stdout', () => {
  const dir = makeTmpDir();
  const r = invokeHook({ hook_event_name: 'SubagentStop', agent_type: 'acceptance', session_id: 's1', agent_id: 'a1', cwd: dir });
  assert.equal(r.status, 0);
  assert.equal(r.stdout.trim(), '');
});

// ─── Test 2: dev agent, tests pass → additionalContext JSON, exit 0 ──────────
test('dev agent, tests pass → additionalContext JSON, exit 0', () => {
  const isWin = process.platform === 'win32';
  const passCmd = isWin ? 'exit 0' : 'true';
  const dir = makeTmpDir({
    '.mercury/config/test-gate.yaml': `test_command: ${passCmd}\n`,
  });
  const r = invokeHook({ hook_event_name: 'SubagentStop', agent_type: 'dev', session_id: 's2', agent_id: 'a2', cwd: dir });
  assert.equal(r.status, 0, `stderr: ${r.stderr}`);
  const parsed = JSON.parse(r.stdout.trim());
  assert.equal(parsed.hookSpecificOutput.hookEventName, 'SubagentStop');
  assert.ok(parsed.hookSpecificOutput.additionalContext.includes('passed'), `additionalContext: ${parsed.hookSpecificOutput.additionalContext}`);
});

// ─── Test 3: dev agent, tests fail → block decision JSON ─────────────────────
test('dev agent, tests fail → block decision JSON, exit 0', () => {
  const isWin = process.platform === 'win32';
  const failCmd = isWin ? 'exit 1' : 'false';
  const dir = makeTmpDir({
    '.mercury/config/test-gate.yaml': `test_command: ${failCmd}\n`,
  });
  const r = invokeHook({ hook_event_name: 'SubagentStop', agent_type: 'dev', session_id: 's3', agent_id: 'a3', cwd: dir });
  assert.equal(r.status, 0, `stderr: ${r.stderr}`);
  const parsed = JSON.parse(r.stdout.trim());
  assert.equal(parsed.decision, 'block');
  assert.ok(typeof parsed.reason === 'string');
});

// ─── Test 4: timeout → block ──────────────────────────────────────────────────
test('test command exceeds timeout → block', () => {
  const isWin = process.platform === 'win32';
  // Sleep 10s but timeout is 1s
  const slowCmd = isWin ? 'ping -n 11 127.0.0.1 > nul' : 'sleep 10';
  const dir = makeTmpDir({
    '.mercury/config/test-gate.yaml': `test_command: ${slowCmd}\n`,
  });
  const r = invokeHook(
    { hook_event_name: 'SubagentStop', agent_type: 'dev', session_id: 's4', agent_id: 'a4', cwd: dir },
    { MERCURY_TEST_GATE_TIMEOUT_SEC: '1' }
  );
  assert.equal(r.status, 0, `stderr: ${r.stderr}`);
  const parsed = JSON.parse(r.stdout.trim());
  assert.equal(parsed.decision, 'block');
  assert.ok(parsed.reason.includes('timed out'));
});

// ─── Test 5: no test command, fail-open → warning + exit 0 ───────────────────
test('no test command, default fail-open → warning stderr, exit 0, no stdout', () => {
  const dir = makeTmpDir(); // no package.json, no convention file, no Cargo.toml, etc.
  const r = invokeHook({ hook_event_name: 'SubagentStop', agent_type: 'dev', session_id: 's5', agent_id: 'a5', cwd: dir });
  assert.equal(r.status, 0, `stderr: ${r.stderr}`);
  assert.equal(r.stdout.trim(), '');
  assert.ok(r.stderr.includes('WARNING'), `expected WARNING in stderr, got: ${r.stderr}`);
});

// ─── Test 6: no test command, STRICT=1 → block ───────────────────────────────
test('no test command + MERCURY_TEST_GATE_STRICT=1 → block', () => {
  const dir = makeTmpDir();
  const r = invokeHook(
    { hook_event_name: 'SubagentStop', agent_type: 'dev', session_id: 's6', agent_id: 'a6', cwd: dir },
    { MERCURY_TEST_GATE_STRICT: '1' }
  );
  assert.equal(r.status, 0);
  const parsed = JSON.parse(r.stdout.trim());
  assert.equal(parsed.decision, 'block');
  assert.ok(parsed.reason.includes('STRICT'));
});

// ─── Test 7: stop_hook_active=true, attempt count < 3 → block with warning ───
test('stop_hook_active=true, count < 3 → block re-entry', () => {
  const isWin = process.platform === 'win32';
  const failCmd = isWin ? 'exit 1' : 'false';
  const dir = makeTmpDir({
    '.mercury/config/test-gate.yaml': `test_command: ${failCmd}\n`,
  });
  // First re-entry attempt
  const r = invokeHook({
    hook_event_name: 'SubagentStop',
    agent_type: 'dev',
    stop_hook_active: true,
    session_id: 'reentry-s7',
    agent_id: 'reentry-a7',
    cwd: dir,
  });
  assert.equal(r.status, 0);
  const parsed = JSON.parse(r.stdout.trim());
  assert.equal(parsed.decision, 'block');
  assert.ok(parsed.reason.includes('re-entry'), `reason: ${parsed.reason}`);
});

// ─── Test 8: stop_hook_active=true, attempt count >= 3 → let through ─────────
test('stop_hook_active=true, count >= 3 → no-op + audit log', () => {
  const dir = makeTmpDir();
  const sessionId = `audit-s8-${Date.now()}`;
  const agentId = 'audit-a8';

  // Pre-seed the state file with count=3
  const stateFile = path.join(dir, '.mercury', 'state', 'test-gate-attempts.json');
  fs.mkdirSync(path.dirname(stateFile), { recursive: true });
  const key = `${sessionId}:${agentId}`;
  fs.writeFileSync(stateFile, JSON.stringify({ [key]: { count: 3, first_attempt_at: new Date().toISOString() } }), 'utf8');

  const r = invokeHook({
    hook_event_name: 'SubagentStop',
    agent_type: 'dev',
    stop_hook_active: true,
    session_id: sessionId,
    agent_id: agentId,
    cwd: dir,
  });
  assert.equal(r.status, 0);
  assert.equal(r.stdout.trim(), ''); // no block decision
  assert.ok(r.stderr.includes('AUDIT'), `expected AUDIT in stderr, got: ${r.stderr}`);
});

// ─── Test 9: persistence across 1→2→3 blocks + pass-through ──────────────────
test('re-entry 1→2→3 persistence: count increments across invocations', () => {
  const dir = makeTmpDir({ 'package.json': JSON.stringify({ scripts: { test: 'node -e "process.exit(1)"' } }) });
  const sessionId = 'persist-s1';
  const agentId = 'persist-a1';
  const input = { hook_event_name: 'SubagentStop', agent_type: 'dev', stop_hook_active: true, session_id: sessionId, agent_id: agentId, cwd: dir };

  // Attempt 1: block with count 1
  let r = invokeHook(input);
  assert.equal(r.status, 0);
  assert.match(r.stdout, /re-entry block 1\/3/);

  // Attempt 2: block with count 2
  r = invokeHook(input);
  assert.equal(r.status, 0);
  assert.match(r.stdout, /re-entry block 2\/3/);

  // Attempt 3: block with count 3
  r = invokeHook(input);
  assert.equal(r.status, 0);
  assert.match(r.stdout, /re-entry block 3\/3/);

  // Attempt 4: pass-through + audit, state cleared
  r = invokeHook(input);
  assert.equal(r.status, 0);
  assert.equal(r.stdout.trim(), '');
  assert.ok(r.stderr.includes('AUDIT'), `expected AUDIT after 3 blocks, got: ${r.stderr}`);

  // Verify state file has the key deleted after pass-through
  const stateFile = path.join(dir, '.mercury', 'state', 'test-gate-attempts.json');
  const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
  assert.equal(state[`${sessionId}:${agentId}`], undefined, 'state should be cleared after pass-through');
});

// ─── Test 10: tests-pass clears stale retry counter ──────────────────────────
test('successful test run clears stale retry counter from prior blocks', () => {
  const dir = makeTmpDir({ 'package.json': JSON.stringify({ scripts: { test: 'node -e "process.exit(0)"' } }) });
  const sessionId = 'clear-s1';
  const agentId = 'clear-a1';
  const stateFile = path.join(dir, '.mercury', 'state', 'test-gate-attempts.json');

  // Pre-seed the state file as if a prior block happened (count=2)
  fs.mkdirSync(path.dirname(stateFile), { recursive: true });
  fs.writeFileSync(stateFile, JSON.stringify({
    [`${sessionId}:${agentId}`]: { count: 2, first_attempt_at: new Date().toISOString() },
  }));

  // Invoke with stop_hook_active=false (first-time stop attempt) + green tests
  const r = invokeHook({ hook_event_name: 'SubagentStop', agent_type: 'dev', stop_hook_active: false, session_id: sessionId, agent_id: agentId, cwd: dir });
  assert.equal(r.status, 0);
  // pass path now emits additionalContext (not block) — confirm no block decision
  const parsed = JSON.parse(r.stdout.trim());
  assert.ok(!parsed.decision, 'should not have decision:block on green run');
  assert.ok(parsed.hookSpecificOutput.additionalContext.includes('passed'));

  // The stale counter should be cleared
  const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
  assert.equal(state[`${sessionId}:${agentId}`], undefined, 'stale retry state should be cleared on green test run');
});

// ─── Test 11: pass-path additionalContext JSON shape ─────────────────────────
test('pass path emits hookSpecificOutput with hookEventName=SubagentStop and additionalContext containing "passed"', () => {
  const isWin = process.platform === 'win32';
  const passCmd = isWin ? 'exit 0' : 'true';
  const dir = makeTmpDir({
    '.mercury/config/test-gate.yaml': `test_command: ${passCmd}\n`,
  });
  const r = invokeHook({ hook_event_name: 'SubagentStop', agent_type: 'dev', session_id: 's11', agent_id: 'a11', cwd: dir });
  assert.equal(r.status, 0, `stderr: ${r.stderr}`);
  const parsed = JSON.parse(r.stdout.trim());
  assert.ok(parsed.hookSpecificOutput, 'must have hookSpecificOutput');
  assert.equal(parsed.hookSpecificOutput.hookEventName, 'SubagentStop');
  assert.ok(typeof parsed.hookSpecificOutput.additionalContext === 'string', 'additionalContext must be string');
  assert.ok(parsed.hookSpecificOutput.additionalContext.includes('passed'), `must contain "passed": ${parsed.hookSpecificOutput.additionalContext}`);
  assert.ok(!parsed.decision, 'must not have decision field on pass path');
});

// ─── Test 12: research-stop-nudge.sh with env=1 → additionalContext ───────────
test('research-stop-nudge.sh: MERCURY_RESEARCH_STOP_NUDGE=1 → additionalContext JSON', () => {
  const nudgeScript = path.resolve(__dirname, '..', '..', '..', '.claude', 'hooks', 'research-stop-nudge.sh');
  const input = JSON.stringify({ hook_event_name: 'SubagentStop', agent_type: 'research', session_id: 'rn1', agent_id: 'ra1' });
  const result = spawnSync('bash', [nudgeScript], {
    input,
    env: { ...process.env, MERCURY_RESEARCH_STOP_NUDGE: '1' },
    encoding: 'utf8',
    timeout: 10000,
  });
  assert.equal(result.status, 0, `stderr: ${result.stderr}`);
  const parsed = JSON.parse((result.stdout || '').trim());
  assert.equal(parsed.hookSpecificOutput.hookEventName, 'SubagentStop');
  assert.ok(typeof parsed.hookSpecificOutput.additionalContext === 'string');
  assert.ok(parsed.hookSpecificOutput.additionalContext.length > 0);
});

// ─── Test 13: research-stop-nudge.sh without env → silent exit 0 ─────────────
test('research-stop-nudge.sh: MERCURY_RESEARCH_STOP_NUDGE unset → silent exit 0', () => {
  const nudgeScript = path.resolve(__dirname, '..', '..', '..', '.claude', 'hooks', 'research-stop-nudge.sh');
  const input = JSON.stringify({ hook_event_name: 'SubagentStop', agent_type: 'research', session_id: 'rn2', agent_id: 'ra2' });
  const envWithout = { ...process.env };
  delete envWithout.MERCURY_RESEARCH_STOP_NUDGE;
  const result = spawnSync('bash', [nudgeScript], {
    input,
    env: envWithout,
    encoding: 'utf8',
    timeout: 10000,
  });
  assert.equal(result.status, 0, `stderr: ${result.stderr}`);
  assert.equal((result.stdout || '').trim(), '', 'should produce no stdout when env unset');
});
