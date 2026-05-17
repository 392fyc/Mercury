'use strict';

// truncate.test.cjs — Issue #300 M4: surrogate-pair-safe Telegram payload
// truncation. The router used to call text.slice(0, 4090), which cuts on
// UTF-16 code units and can split a surrogate pair at the boundary,
// producing a half-pair that Telegram rejects with 400. Verify the
// extracted helper preserves code-point integrity and keeps the result
// within the 4096-code-unit Telegram cap.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { truncateForTelegram } = require('../lib/truncate.cjs');

// Check that a UTF-16 string has no unpaired surrogates. Returns true if
// every high surrogate (D800-DBFF) is immediately followed by a low
// surrogate (DC00-DFFF), and no low surrogate appears without a preceding
// high. This is what Telegram's API enforces — a half-pair anywhere in
// the message body trips a 400.
function hasValidUtf16(s) {
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xD800 && c <= 0xDBFF) {
      const next = s.charCodeAt(i + 1);
      if (!(next >= 0xDC00 && next <= 0xDFFF)) return false;
      i++;
    } else if (c >= 0xDC00 && c <= 0xDFFF) {
      return false;
    }
  }
  return true;
}

test('short text under cap returns unchanged', () => {
  const s = 'hello world';
  assert.equal(truncateForTelegram(s), s);
});

test('text at exactly 4096 code units returns unchanged', () => {
  const s = 'a'.repeat(4096);
  assert.equal(truncateForTelegram(s), s);
  assert.equal(truncateForTelegram(s).length, 4096);
});

test('ASCII text over cap truncates to <= 4096 with ellipsis', () => {
  const s = 'a'.repeat(5000);
  const out = truncateForTelegram(s);
  assert.ok(out.length <= 4096, `length ${out.length} exceeds 4096`);
  assert.ok(out.endsWith('…'), 'must end with ellipsis');
  assert.equal(out.length, 4096, 'ASCII case should fill to cap');
});

test('surrogate pair at boundary is not split (#300 main regression)', () => {
  // 4089 ASCII + 1 emoji (2 code units) at the cut. Naive slice(0, 4090)
  // would take 4089 'a' + first half of the emoji = invalid UTF-16.
  const emoji = '😀'; // U+1F600 GRINNING FACE
  const s = 'a'.repeat(4089) + emoji + 'b'.repeat(2000);
  assert.equal(s.length, 4089 + 2 + 2000);
  const out = truncateForTelegram(s);
  assert.ok(hasValidUtf16(out), 'truncated payload must be valid UTF-16');
  assert.ok(out.length <= 4096, `length ${out.length} exceeds 4096`);
  assert.ok(out.endsWith('…'));
});

test('all-supplementary input stays within UTF-16 cap', () => {
  // Telegram's cap is 4096 UTF-16 code units. A naive Array.from(...).slice(0,4090)
  // could yield 8181 code units (4090 emoji * 2 + '…') and still 400. Verify
  // the budget-aware helper caps the result strictly.
  const emoji = '😀';
  const s = emoji.repeat(3000); // 6000 code units
  const out = truncateForTelegram(s);
  assert.ok(hasValidUtf16(out), 'must be valid UTF-16');
  assert.ok(out.length <= 4096, `length ${out.length} exceeds 4096`);
  assert.ok(out.endsWith('…'));
});

test('mixed BMP + supplementary respects byte budget', () => {
  // Interleave so the budget runs out partway and the last picked code
  // point may be supplementary.
  let s = '';
  for (let i = 0; i < 3000; i++) s += (i % 2 ? 'x' : '😀');
  const out = truncateForTelegram(s);
  assert.ok(hasValidUtf16(out), 'must be valid UTF-16');
  assert.ok(out.length <= 4096, `length ${out.length} exceeds 4096`);
});

test('empty string returns empty', () => {
  assert.equal(truncateForTelegram(''), '');
});

test('non-string input passes through (defensive)', () => {
  assert.equal(truncateForTelegram(null), null);
  assert.equal(truncateForTelegram(undefined), undefined);
  assert.equal(truncateForTelegram(123), 123);
});

test('custom cap honors smaller budget', () => {
  const s = 'a'.repeat(100);
  const out = truncateForTelegram(s, 10);
  assert.ok(out.length <= 10);
  assert.ok(out.endsWith('…'));
});

test('zero or sub-ellipsis cap drops the ellipsis (Codex Low finding)', () => {
  // Codex audit: with max < ellipsis.length the budget went negative and the
  // ellipsis itself overflowed the cap. Guard the degenerate case.
  assert.equal(truncateForTelegram('abc', 0), '', 'max=0 returns empty');
  // max=2 with a 3-char ellipsis: must not return '...' (length 3 > 2).
  const out = truncateForTelegram('abcdef', 2, '...');
  assert.ok(out.length <= 2, `length ${out.length} exceeds max=2`);
  assert.ok(!out.includes('...'), 'ellipsis dropped when it cannot fit');
});
