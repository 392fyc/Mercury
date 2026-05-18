'use strict';

// Telegram bot wiring — initBot() constructs the bot, tgSend() sends messages
// with bounded retry. Extracted from router.cjs per #303. State (the live
// `bot` instance) lives on the shared state object so the IPC handler module
// and the routing module can both reach it without an import cycle.

const state = require('./state.cjs');
const { truncateForTelegram } = require('./truncate.cjs');
const { isEnvTruthy } = require('./env.cjs');

const { TAG } = state;

// Issue #304: TG_SEND retry bounds.
const TG_SEND_MAX_RETRIES       = 2; // tgSend retry budget
const TG_SEND_RETRY_AFTER_MAX_S = 5; // max seconds to honor Telegram retry_after hint

const BOT_TOKEN = process.env.MERCURY_TELEGRAM_BOT_TOKEN;

// Allowlist — fail-closed: empty set blocks all inbound messages.
const ALLOWED = new Set(
  (process.env.MERCURY_TELEGRAM_ALLOWED_USER_IDS || '').split(',').map(s => s.trim()).filter(Boolean),
);
const isAllowed = id => ALLOWED.has(String(id));
if (BOT_TOKEN && ALLOWED.size === 0) {
  process.stderr.write(`${TAG} WARNING: ALLOWED user IDs empty; ALL inbound Telegram messages will be dropped. Set MERCURY_TELEGRAM_ALLOWED_USER_IDS to enable.\n`);
}

// HTML escape helper — applied to all user-controlled interpolations in tgSend calls.
const htmlEsc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// Issue #304 nit 4: bot polling is started by initBot() inside the
// server.listen callback, AFTER acquireLock() establishes that this process
// owns the singleton lock. Previously the `new TelegramBot({polling:true})`
// call lived at module load, so a second router started during startup would
// briefly poll the same getUpdates queue and hit a Telegram 409 conflict in
// the window between module init and the server.listen EADDRINUSE handler.
// Issue #298: strict truthy check — '0', 'false', 'no', 'off', '', unset → enabled.
// Caller (router.cjs entry) is responsible for wiring `state.bot.on('message',
// routeMessage)` after this returns — that listener cannot live in this
// module without re-introducing a telegram → routing import cycle.
function initBot(onMessage) {
  if (isEnvTruthy(process.env.MERCURY_NOTIFY_DISABLED)) return;
  if (!BOT_TOKEN) {
    process.stderr.write(`${TAG} WARNING: MERCURY_TELEGRAM_BOT_TOKEN not set; Telegram disabled\n`);
    return;
  }
  try {
    const TelegramBot = require('node-telegram-bot-api');
    state.bot = new TelegramBot(BOT_TOKEN, { polling: true });
    state.bot.on('polling_error', e => process.stderr.write(`${TAG} polling error: ${e.message}\n`));
    if (typeof onMessage === 'function') state.bot.on('message', onMessage);
    process.stderr.write(`${TAG} Telegram polling started\n`);
  } catch (e) {
    process.stderr.write(`${TAG} Telegram init failed: ${e.message}\n`);
  }
}

async function tgSend(chatId, text) {
  if (!state.bot) return;
  // Coerce at the API boundary so non-string callers (defensive) and
  // null/undefined become an empty string; skip empty payloads instead of
  // letting Telegram 400 on them and retrying.
  const payload = truncateForTelegram(String(text ?? ''));
  if (!payload) return;
  for (let attempt = 0; attempt < TG_SEND_MAX_RETRIES; attempt++) {
    try {
      await state.bot.sendMessage(chatId, payload, { parse_mode: 'HTML' });
      return;
    } catch (e) {
      const ra = Number(e?.response?.body?.parameters?.retry_after);
      if (attempt === 0 && Number.isFinite(ra) && ra > 0 && ra <= TG_SEND_RETRY_AFTER_MAX_S) {
        await new Promise(r => setTimeout(r, ra * 1000));
        continue;
      }
      process.stderr.write(`${TAG} sendMessage error (attempt ${attempt + 1}): ${e.message}\n`);
      return;
    }
  }
}

module.exports = {
  BOT_TOKEN, ALLOWED, isAllowed, htmlEsc, initBot, tgSend,
  TG_SEND_MAX_RETRIES, TG_SEND_RETRY_AFTER_MAX_S,
};
