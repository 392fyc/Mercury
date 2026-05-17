'use strict';

// truncate.cjs — surrogate-pair-safe Telegram payload truncation (#300).
//
// Telegram Bot API sendMessage cap = 4096 UTF-16 code units. The naive
// `text.slice(0, 4090)` cuts on UTF-16 code units, so a surrogate pair
// (emoji / supplementary code point) straddling the cut produces an invalid
// half-pair → Telegram API 400. Iterate by Unicode code points (for...of
// over a string) so surrogate pairs stay intact, and track running UTF-16
// length so the trimmed string + ellipsis still fits the cap regardless of
// the input mix (worst case: all supplementary code points, 2 code units
// each).

const TG_MAX = 4096;
const ELLIPSIS = '…';

function truncateForTelegram(text, max = TG_MAX, ellipsis = ELLIPSIS) {
  // Normalize `max` defensively: a NaN, negative, or non-integer value would
  // make budget arithmetic unstable and could let the result exceed the cap.
  // Fall back to TG_MAX when the caller passes garbage.
  const cap = Number.isFinite(max) ? Math.max(0, Math.floor(max)) : TG_MAX;
  if (typeof text !== 'string') return text;
  if (text.length <= cap) return text;
  // If the marker is at least as long as the cap, drop it — appending it would
  // itself overflow `cap`. The caller asked for a budget smaller than the
  // marker, so the marker has to go.
  const useEllipsis = ellipsis.length < cap;
  const budget = useEllipsis ? cap - ellipsis.length : cap;
  let acc = '';
  let utf16Len = 0;
  for (const cp of text) {
    const cpLen = cp.length;
    if (utf16Len + cpLen > budget) break;
    acc += cp;
    utf16Len += cpLen;
  }
  return useEllipsis ? acc + ellipsis : acc;
}

module.exports = { truncateForTelegram, TG_MAX, ELLIPSIS };
