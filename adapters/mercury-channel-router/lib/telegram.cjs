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

// Argus #408 iter-1 Critical: redact BOT_TOKEN from any error message before
// writing to stderr. grammy's GrammyError.message excludes the request URL,
// but HttpError wraps fetch errors whose causes may surface the URL (and
// therefore the path-embedded bot token). Defense-in-depth: strip the literal
// token from every log line so a future grammy/node-fetch change cannot leak
// credentials via stderr.
const sanitize = msg => BOT_TOKEN ? String(msg).replaceAll(BOT_TOKEN, '<REDACTED>') : String(msg);

// Issue #302: switched from node-telegram-bot-api (pulled deprecated
// request@2.88.2 + har-validator transitive stack) to grammy@1.x (MIT, 4
// direct deps, drops the entire legacy `request` chain). Same singleton lock
// gate as #304 nit 4 — `bot.start()` is called from initBot() inside the
// server.listen callback AFTER acquireLock() establishes ownership. Caller
// passes the message handler in via `onMessage`; we wrap it as
// `ctx => onMessage(ctx.update.message)` so the raw Telegram Message object
// flows through unchanged (routing.cjs::routeMessage expects msg.from /
// msg.chat / msg.text). Accepting the handler as a parameter (rather than a
// `require('./routing.cjs')` at module load) avoids a
// telegram.cjs → routing.cjs → telegram.cjs import cycle.
// Issue #298: strict truthy check — '0', 'false', 'no', 'off', '', unset → enabled.
function initBot(onMessage) {
  // Argus #408 iter-1 Medium (4): idempotency guard. The expected call path is
  // exactly once from server.listen's callback after acquireLock(), but a
  // future caller could repeat the call (manual restart hook, test harness,
  // future hot-reload). Without this guard, a second call would register a
  // duplicate `message` handler AND start a second long-poll loop, hitting
  // Telegram's 409 conflict on getUpdates. The structural test in
  // housekeeping-304.test.cjs:103 still asserts the single-construction
  // architecture; this guard is defense-in-depth at runtime.
  if (state.bot) return;
  if (isEnvTruthy(process.env.MERCURY_NOTIFY_DISABLED)) return;
  if (!BOT_TOKEN) {
    process.stderr.write(`${TAG} WARNING: MERCURY_TELEGRAM_BOT_TOKEN not set; Telegram disabled\n`);
    return;
  }
  try {
    const { Bot } = require('grammy');
    state.bot = new Bot(BOT_TOKEN);
    // grammy's bot.catch() handles errors thrown inside middleware. Fatal
    // startup errors (auth failure, malformed token, immediate getMe failure)
    // reject the bot.start() Promise — captured by the .catch() below.
    // Steady-state long-poll fetch retries are handled internally by grammy
    // and do not surface here; log volume is lower than node-telegram-bot-api's
    // polling_error firehose by design.
    // Copilot iter-1 C5: grammy wraps the original middleware exception as
    // `BotError`. `BotError.message` contains the cause in parens, but the
    // raw inner Error gives a cleaner one-line grep target ("TypeError: foo"
    // vs "Error in middleware while handling an update with ID 42 (TypeError:
    // foo)"). Prefer the inner cause when available; fall back to the wrapper
    // message otherwise.
    state.bot.catch(err => {
      const msg = err?.error instanceof Error ? err.error.message : err.message;
      process.stderr.write(`${TAG} bot middleware error: ${sanitize(msg)}\n`);
    });
    if (typeof onMessage === 'function') {
      state.bot.on('message', ctx => onMessage(ctx.update.message));
    }
    // bot.start() returns a Promise that resolves on bot.stop(). We do not
    // await — long-poll runs concurrently with the HTTP server. A rejected
    // start (network failure) is logged but does not crash the router so
    // /register + non-Telegram IPC routes stay functional.
    // Codex sync-audit M1 (Issue #302): grammy default `drop_pending_updates:
    // false` is intentionally inherited — node-telegram-bot-api initializes
    // its long-poll offset to 0, which causes updates queued during router
    // downtime (verdicts, commands, replies) to be replayed after restart.
    // Setting `drop_pending_updates: true` here would discard those messages
    // and silently lose user input across restarts.
    state.bot.start({
      onStart: () => process.stderr.write(`${TAG} Telegram polling started\n`),
    }).catch(err => {
      // Argus #408 iter-1 Medium (3): clear state.bot so subsequent tgSend
      // calls short-circuit at the `if (!state.bot)` guard instead of firing
      // doomed requests against a dead instance and writing a fresh stderr
      // line per attempt. The bot is effectively unusable after a fatal
      // start failure; making it explicit is cheaper than detecting "is the
      // poll loop still alive" via a side-channel.
      process.stderr.write(`${TAG} bot poll fatal: ${sanitize(err.message)}\n`);
      state.bot = null;
    });
  } catch (e) {
    process.stderr.write(`${TAG} Telegram init failed: ${sanitize(e.message)}\n`);
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
      await state.bot.api.sendMessage(chatId, payload, { parse_mode: 'HTML' });
      return;
    } catch (e) {
      // grammy GrammyError exposes Telegram's `parameters.retry_after`
      // directly on the error object (vs node-telegram-bot-api's nested
      // `e.response.body.parameters.retry_after`). Narrow on GrammyError so
      // a non-Telegram-API failure (e.g. HttpError carrying an unrelated
      // `parameters` field) cannot accidentally honor a retry hint.
      const { GrammyError } = require('grammy');
      const ra = e instanceof GrammyError ? Number(e?.parameters?.retry_after) : NaN;
      if (attempt === 0 && Number.isFinite(ra) && ra > 0 && ra <= TG_SEND_RETRY_AFTER_MAX_S) {
        await new Promise(r => setTimeout(r, ra * 1000));
        continue;
      }
      process.stderr.write(`${TAG} sendMessage error (attempt ${attempt + 1}): ${sanitize(e.message)}\n`);
      return;
    }
  }
}

// Copilot iter-4 finding (#406): `BOT_TOKEN` is intentionally NOT exported.
// It is a secret-bearing env var used internally by initBot(); exporting it
// (even just for symmetry with ALLOWED) would widen the IPC surface and risk
// accidental serialization in future debug logging. ALLOWED + isAllowed are
// fine to export because they're already derivable from env.
module.exports = {
  ALLOWED, isAllowed, htmlEsc, initBot, tgSend,
  TG_SEND_MAX_RETRIES, TG_SEND_RETRY_AFTER_MAX_S,
};
