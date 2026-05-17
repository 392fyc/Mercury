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
  if (typeof text !== 'string') return text;
  if (text.length <= max) return text;
  // If the marker is at least as long as the cap, drop it — appending it would
  // itself overflow `max`. The caller asked for a budget smaller than the
  // marker, so the marker has to go.
  const useEllipsis = ellipsis.length < max;
  const budget = useEllipsis ? max - ellipsis.length : max;
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
