'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { isEnvTruthy } = require('../lib/env.cjs');

test('isEnvTruthy: canonical truthy strings', () => {
  for (const v of ['1', 'true', 'yes', 'on']) {
    assert.strictEqual(isEnvTruthy(v), true, `expected ${JSON.stringify(v)} truthy`);
  }
});

test('isEnvTruthy: case-insensitive', () => {
  for (const v of ['TRUE', 'True', 'Yes', 'YES', 'On', 'ON']) {
    assert.strictEqual(isEnvTruthy(v), true, `expected ${JSON.stringify(v)} truthy`);
  }
});

test('isEnvTruthy: whitespace trimmed', () => {
  for (const v of [' 1', '1 ', '  true  ', '\tyes\n', '\non\t']) {
    assert.strictEqual(isEnvTruthy(v), true, `expected trim(${JSON.stringify(v)}) truthy`);
  }
});

test('isEnvTruthy: literal 0 is NOT truthy (Issue #298 core bug)', () => {
  assert.strictEqual(isEnvTruthy('0'), false);
  assert.strictEqual(isEnvTruthy(' 0 '), false);
});

test('isEnvTruthy: explicit falsy strings', () => {
  for (const v of ['false', 'False', 'FALSE', 'no', 'No', 'NO', 'off', 'Off']) {
    assert.strictEqual(isEnvTruthy(v), false, `expected ${JSON.stringify(v)} falsy`);
  }
});

test('isEnvTruthy: arbitrary strings falsy', () => {
  for (const v of ['random', 'enable', 'disable', '2', '-1', 'truthy', 'y', 'n']) {
    assert.strictEqual(isEnvTruthy(v), false, `expected ${JSON.stringify(v)} falsy`);
  }
});

test('isEnvTruthy: empty / null / undefined are falsy', () => {
  assert.strictEqual(isEnvTruthy(''), false);
  assert.strictEqual(isEnvTruthy('   '), false);
  assert.strictEqual(isEnvTruthy(null), false);
  assert.strictEqual(isEnvTruthy(undefined), false);
});

test('isEnvTruthy: non-string inputs coerced via String()', () => {
  assert.strictEqual(isEnvTruthy(1), true);
  assert.strictEqual(isEnvTruthy(0), false);
  assert.strictEqual(isEnvTruthy(true), true);
  assert.strictEqual(isEnvTruthy(false), false);
});
