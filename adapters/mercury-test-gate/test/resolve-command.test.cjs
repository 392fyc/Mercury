'use strict';
// resolve-command.test.cjs — Tests for convention file and auto-detect logic
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const os = require('os');

const { resolveCommand, parseConventionFile, autoDetect } = require('../lib/resolve-command.cjs');

function makeTmpDir(files = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mtg-resolve-'));
  for (const [rel, content] of Object.entries(files)) {
    const full = path.join(dir, rel);
    fs.mkdirSync(path.dirname(full), { recursive: true });
    fs.writeFileSync(full, content, 'utf8');
  }
  return dir;
}

// ─── Test 9: convention file wins over auto-detect ───────────────────────────
test('convention file wins over package.json auto-detect', () => {
  const dir = makeTmpDir({
    '.mercury/config/test-gate.yaml': 'test_command: my-custom-test-runner\n',
    'package.json': JSON.stringify({ scripts: { test: 'jest' } }),
  });
  const cmd = resolveCommand(dir);
  assert.equal(cmd, 'my-custom-test-runner');
});

// ─── Test 10: auto-detect fallback order ─────────────────────────────────────
test('auto-detect: package.json scripts.test wins when no convention file', () => {
  const dir = makeTmpDir({
    'package.json': JSON.stringify({ scripts: { test: 'jest' } }),
  });
  assert.equal(resolveCommand(dir), 'npm test');
});

test('auto-detect: pyproject.toml fallback when no package.json', () => {
  const dir = makeTmpDir({
    'pyproject.toml': '[tool.pytest.ini_options]\naddopts = "-v"\n',
  });
  assert.equal(resolveCommand(dir), 'python -m pytest');
});

test('auto-detect: Makefile test target fallback', () => {
  const dir = makeTmpDir({
    'Makefile': 'test:\n\techo running tests\n',
  });
  assert.equal(resolveCommand(dir), 'make test');
});

test('auto-detect: Cargo.toml fallback', () => {
  const dir = makeTmpDir({
    'Cargo.toml': '[package]\nname = "example"\n',
  });
  assert.equal(resolveCommand(dir), 'cargo test');
});

test('auto-detect: null when nothing matches', () => {
  const dir = makeTmpDir(); // empty dir
  assert.equal(resolveCommand(dir), null);
});

test('convention file: quoted value is unquoted', () => {
  const dir = makeTmpDir({
    '.mercury/config/test-gate.yaml': 'test_command: "npm run test:ci"\n',
  });
  assert.equal(resolveCommand(dir), 'npm run test:ci');
});

test('convention file: single-quoted value is unquoted', () => {
  const dir = makeTmpDir({
    '.mercury/config/test-gate.yaml': "test_command: 'yarn test'\n",
  });
  assert.equal(resolveCommand(dir), 'yarn test');
});

test('convention file: returns null for missing file', () => {
  const dir = makeTmpDir();
  const conventionPath = path.join(dir, '.mercury', 'config', 'test-gate.yaml');
  assert.equal(parseConventionFile(conventionPath), null);
});

test('auto-detect: skips default npm test placeholder', () => {
  const dir = makeTmpDir({
    'package.json': JSON.stringify({ scripts: { test: 'echo "Error: no test specified" && exit 1' } }),
  });
  // Should not return npm test for the default placeholder
  const cmd = autoDetect(dir);
  assert.notEqual(cmd, 'npm test');
});
