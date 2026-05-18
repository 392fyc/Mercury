'use strict';

// Issue #307: Telegram inline_keyboard for permission approvals (CCGram UX).
// Wired both ways:
//   - ipc.cjs /permission-request uses buildVerdictKeyboard to attach
//     [✅ Allow] [❌ Deny] buttons to the outbound message.
//   - router.cjs passes routeCallback into telegram.initBot so grammy's
//     bot.on('callback_query:data', ctx => routeCallback(ctx)) dispatches
//     the verdict when the user taps a button.
//
// Compact callback_data format `v:<y|n>:<short>-<rid>` (≤64 bytes per
// Telegram's InlineKeyboardButton.callback_data hard limit; CCGram uses
// the same `type:promptId:action` shape). Mercury's prefixed_id is
// `[a-z0-9]{6}-[a-km-z]{5}` = 12 chars, so the full callback_data is
// ~16 bytes — well under 64 even with future verdict types.
//
// Text-reply path in routing.cjs::routeMessage stays intact as the
// documented fallback (per Issue #307 acceptance: "Must keep text-reply
// path as fallback (some clients render buttons poorly)").

const state = require('./state.cjs');
const { isAllowed } = require('./telegram.cjs');

// Reuse the same prefixed_id shape that routing.cjs::routeMessage validates
// for text-reply verdicts: 6 lowercase alphanumeric + dash + 5 lowercase
// a-z minus 'l'. Anchored so a malformed callback (truncated, padded, or
// containing extra `:`) cannot smuggle a partial id past the parser.
// Codex audit (Medium): the same shape gates /permission-request input at
// the IPC boundary too — without it, an upstream drift in prefixed_request_id
// shape would silently produce an unparsable button (sendMessage 400) while
// the IPC endpoint still returns ok:true.
const PREFIXED_REQUEST_ID_RE = /^[a-z0-9]{6}-[a-km-z]{5}$/;
const VERDICT_CB_RE = /^v:([yn]):([a-z0-9]{6})-([a-km-z]{5})$/;
const isValidPrefixedRequestId = s => typeof s === 'string' && PREFIXED_REQUEST_ID_RE.test(s);

const parseVerdictCallback = data => {
  if (typeof data !== 'string') return null;
  const m = data.match(VERDICT_CB_RE);
  if (!m) return null;
  return {
    verdict: m[1] === 'y' ? 'yes' : 'no',
    shortId: m[2],
    requestId: m[3],
  };
};

// Plain object shape — grammy passes reply_markup verbatim to the Bot API,
// and `{inline_keyboard: [[...]]}` is exactly what the InlineKeyboard helper
// class serializes to. Keeping it dep-free of grammy means callback.cjs is
// importable from the IPC layer without dragging in the bot SDK.
const buildVerdictKeyboard = prefixed_request_id => ({
  inline_keyboard: [[
    { text: '✅ Allow', callback_data: `v:y:${prefixed_request_id}` },
    { text: '❌ Deny',  callback_data: `v:n:${prefixed_request_id}` },
  ]],
});

// Message body for /permission-request. Extracted so ipc.cjs can hold its
// ≤200 LOC budget without packing the template onto one 226-char line
// (Claude review LOW #3). Caller passes htmlEsc so callback.cjs stays
// independent of telegram.cjs's HTML helper.
const buildPermissionRequestText = (tool_name, description, prefixed_request_id, htmlEsc) =>
  `Claude wants to run ${htmlEsc(tool_name)}: ${htmlEsc(description)}\n\nTap a button below, or reply <code>yes ${htmlEsc(prefixed_request_id)}</code> / <code>no ${htmlEsc(prefixed_request_id)}</code>`;

// grammy's `bot.on('callback_query:data', handler)` invokes the handler with
// a ctx whose .callbackQuery has {id, from, data, message}. Telegram clients
// display a progress spinner on the tapped button until the bot calls
// answerCallbackQuery; we always ack, even on bad input, and swallow the ack
// failure (it's a UX nicety — never crash the bot loop because of it).
//
// Allowlist + format validation BEFORE dispatch: a tap from an unauthorized
// user must never inject a verdict event into a session inbox. routeMessage
// applies the same gate to text-reply verdicts (routing.cjs::routeMessage
// line 159, isAllowed check before lastChatId capture).
async function routeCallback(ctx) {
  const q = ctx?.callbackQuery;
  if (!q) return;
  const ack = txt => ctx.answerCallbackQuery(txt ? { text: txt } : undefined).catch(() => {});
  // Codex audit (Low): mirror routing.cjs::routeMessage line 158's MVP rule
  // that the router accepts only private chats. Without this, an allowlisted
  // user could approve verdicts from a group chat that the bot was added to —
  // a transport-level boundary change Issue #307 did not intend. The chat
  // type lives on the originating message; non-data callbacks (no .message)
  // are dropped silently.
  if (q.message?.chat?.type !== 'private') { await ack(); return; }
  if (!isAllowed(q.from?.id)) { await ack(); return; }
  const parsed = parseVerdictCallback(q.data);
  if (!parsed) { await ack('Unrecognized callback'); return; }
  const t = [...state.sessions.values()].find(s => s.shortId === parsed.shortId);
  if (!t) { await ack('Session not found'); return; }
  // Lazy-require to break the ipc.cjs → callback.cjs → routing.cjs → ipc.cjs
  // module load cycle. routing.cjs is fully loaded by the time a real
  // callback fires (the bot can't poll Telegram until acquireLock + initBot
  // run, which is well after every require() finishes).
  const { sendToInbox } = require('./routing.cjs');
  sendToInbox(t.id, { type: 'verdict', verdict: parsed.verdict, request_id: parsed.requestId });
  await ack(parsed.verdict === 'yes' ? 'Allowed' : 'Denied');
}

module.exports = {
  VERDICT_CB_RE, PREFIXED_REQUEST_ID_RE,
  parseVerdictCallback, isValidPrefixedRequestId,
  buildVerdictKeyboard, buildPermissionRequestText, routeCallback,
};
