#!/usr/bin/env node
'use strict';
const fs = require('fs');
const path = require('path');
const { resolveCommand } = require('./lib/resolve-command.cjs');
const { runCommand } = require('./lib/run-command.cjs');
const { checkAndIncrement, clearAttempts } = require('./lib/attempt-tracker.cjs');

const GATED = ['dev'];
const TIMEOUT = parseInt(process.env.MERCURY_TEST_GATE_TIMEOUT_SEC || '300', 10);
const STRICT = process.env.MERCURY_TEST_GATE_STRICT === '1';
const TAG = '[mercury-test-gate]';

const block = (reason) => { process.stdout.write(JSON.stringify({ decision: 'block', reason }) + '\n'); process.exit(0); };
const pass = () => process.exit(0);

async function main() {
  let input;
  try { input = JSON.parse(fs.readFileSync(0, 'utf8')); } catch (_) { pass(); }

  const { stop_hook_active = false, agent_type = '', agent_id = 'unknown', session_id = 'unknown', cwd: inputCwd } = input;
  const cwd = inputCwd || process.cwd();

  // 1. Scope check — only dev agents
  if (!GATED.includes(agent_type)) pass();

  // 2. Re-entry guard with bounded retry (Q14)
  if (stop_hook_active) {
    const { shouldBlock, count } = checkAndIncrement(path.join(cwd, '.mercury', 'state'), session_id, agent_id);
    if (!shouldBlock) {
      process.stderr.write(`${TAG} AUDIT: agent ${agent_id} reached max re-entry blocks (${count}). Letting stop proceed.\n`);
      pass();
    }
    block(`Mercury test gate: re-entry block ${count}/3. Tests were still failing when you last attempted to stop. Fix the failing tests and retry.`);
  }

  // 3. Resolve test command (convention file beats auto-detect)
  const testCmd = resolveCommand(cwd);
  if (!testCmd) {
    if (STRICT) block('Mercury test gate: no test command found and MERCURY_TEST_GATE_STRICT=1. Add .mercury/config/test-gate.yaml with test_command: <cmd>.');
    process.stderr.write(`${TAG} WARNING: no test command resolved; skipping test gate (fail-open). Set MERCURY_TEST_GATE_STRICT=1 to fail-closed.\n`);
    pass();
  }

  // 4. Run with timeout
  const r = await runCommand(testCmd, cwd, TIMEOUT);

  // 5. Interpret
  if (r.timed_out) block(`Mercury test gate: test command timed out after ${TIMEOUT}s.\nCommand: ${testCmd}\nInvestigate the hang before continuing.`);
  if (r.exit_code !== 0) {
    const tail = (r.stdout + '\n' + r.stderr).trim().split('\n').slice(-20).join('\n');
    block(`Mercury test gate: \`${testCmd}\` exited ${r.exit_code}.\nLast output:\n${tail}\nCannot stop while tests are failing. Fix and retry.`);
  }

  // Tests passed — clear any stale retry counters from prior blocked attempts.
  clearAttempts(path.join(cwd, '.mercury', 'state'), session_id, agent_id);
  pass();
}

main().catch((e) => { process.stderr.write(`${TAG} Unexpected error: ${e.message}\n`); process.exit(0); });
