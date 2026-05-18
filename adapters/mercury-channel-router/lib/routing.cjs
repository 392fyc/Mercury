'use strict';

// Routing — Telegram dispatch, command handlers, label derivation, SSE push,
// shutdown scheduler. #303 split from router.cjs.

const path = require('path');
const state = require('./state.cjs');
const { tgSend, htmlEsc, isAllowed } = require('./telegram.cjs');
const { releaseLock, cleanupToken } = require('./ipc.cjs');

const { TAG, MAX_SESS } = state;

// Issue #304: shutdown grace period after the last session deregisters.
const SHUTDOWN_GRACE_MS = 30000;

function deriveLabel({ project_path = '', branch = '' }) {
  const m1 = branch.match(/^feature\/(?:lane-[\w-]+\/)?TASK-(\d+)-([\w-]+)/);
  if (m1) return `#${m1[1]} ${m1[2]}`.slice(0, 30);
  const m2 = branch.match(/^feature\/([\w-]+)/);
  return (m2 ? m2[1] : path.basename(project_path || process.cwd())).slice(0, 30);
}

function sendToInbox(sid, event) {
  const s = state.sessions.get(sid);
  if (!s || !s.sseClients) return;
  const data = `data: ${JSON.stringify(event)}\n\n`;
  // Issue #297: mutate in place (splice) — /register upsert preserves the
  // sseClients array reference, and the /inbox close handler holds it too;
  // reassigning here would detach Map entry from the watched array.
  for (let i = s.sseClients.length - 1; i >= 0; i--) {
    try { s.sseClients[i].write(data); }
    catch { s.sseClients.splice(i, 1); }
  }
}

function scheduleShutdown() {
  if (state.sessions.size > 0) { clearTimeout(state.shutdownTimer); state.shutdownTimer = null; return; }
  if (state.shutdownTimer) return;
  process.stderr.write(`${TAG} all sessions gone; shutting down in ${SHUTDOWN_GRACE_MS}ms\n`);
  state.shutdownTimer = setTimeout(() => { releaseLock(); cleanupToken(); process.exit(0); }, SHUTDOWN_GRACE_MS);
}

// Phase C (#324): resolve session by label prefix. Strict order — exact
// equality > single prefix match (with optional `#` stripped) > ambiguous
// (multiple) > undefined. Returns {match}, {match: undefined}, or
// {ambiguous: [labels]} so the caller can surface candidates rather than
// auto-pick. Never silently disambiguates.
const findByLabel = prefix => {
  const sess = [...state.sessions.values()];
  const exact = sess.find(s => s.label === prefix);
  if (exact) return { match: exact };
  const matches = sess.filter(s =>
    s.label.startsWith(prefix) || s.label.replace(/^#/, '').startsWith(prefix));
  if (matches.length === 0) return { match: undefined };
  if (matches.length === 1) return { match: matches[0] };
  return { ambiguous: matches.map(s => s.label) };
};

// findByLabel + user-facing reply on miss/ambiguous. Returns matched session
// or null (after replying). Used by every lane-targeted command.
async function resolveLane(chatId, prefix) {
  const r = findByLabel(prefix);
  if (r.ambiguous) {
    await tgSend(chatId, `Ambiguous lane @${htmlEsc(prefix)}; candidates: ${r.ambiguous.map(htmlEsc).join(', ')}`);
    return null;
  }
  if (!r.match) {
    await tgSend(chatId, `No session matching @${htmlEsc(prefix)}`);
    return null;
  }
  return r.match;
}

// Phase C: parse `@<lane> <payload>` from cmd args. Returns {lane, payload} or null.
// Lane token allows `#`, word chars and `-` so users can address `@#324` directly
// in addition to the bare numeric form `@324`.
const parseLanePayload = args => {
  if (!args) return null;
  const m = args.match(/^@(#?[\w-]+)\s+(.+)$/s);
  return m ? { lane: m[1].replace(/^#/, ''), payload: m[2].trim() } : null;
};

// Phase C: payload validators (defense-in-depth past the allowlist).
// `dir`: filesystem-safe chars only, no `..`. `model`: alnum + `.` `-` `_`,
// ≤64. `permission-mode`: enum of Claude Code's documented modes.
const PERMISSION_MODES = new Set(['strict', 'standard', 'trust', 'plan', 'edit', 'acceptEdits', 'bypassPermissions', 'default']);
const COMMAND_VALIDATORS = {
  dir:               v => /^[A-Za-z0-9_./:\\-]+$/.test(v) && !v.split(/[/\\]/).includes('..'),
  model:             v => /^[A-Za-z0-9._-]{1,64}$/.test(v),
  'permission-mode': v => PERMISSION_MODES.has(v),
};

// Phase C: relay a command to a lane's inbox; replies to chat with status.
// `usage` is the help text shown when args parse fails.
async function relayLaneCmd(chatId, cmd, args, payloadKey, usage) {
  const lp = parseLanePayload(args);
  if (!lp || !lp.payload) return tgSend(chatId, `Usage: ${usage}`);
  const validator = COMMAND_VALIDATORS[cmd];
  if (validator && !validator(lp.payload)) {
    return tgSend(chatId, `Invalid payload for /${htmlEsc(cmd)}: ${htmlEsc(lp.payload)}`);
  }
  const t = await resolveLane(chatId, lp.lane);
  if (!t) return; // resolveLane already replied (ambiguous OR no-match)
  const event = { type: 'command', cmd, from_chat: chatId };
  event[payloadKey] = lp.payload;
  sendToInbox(t.id, event);
  return tgSend(chatId, `Sent /${htmlEsc(cmd)} ${htmlEsc(lp.payload)} to [${htmlEsc(t.label)}]`);
}

async function handleCmd(chatId, cmd, args) {
  if (cmd === 'status') {
    const a = state.sessions.get(state.activeId);
    return tgSend(chatId, `<b>Mercury Router</b>\nUptime: ${Math.round((Date.now() - state.startTs) / 1000)}s\nSessions: ${state.sessions.size}/${MAX_SESS}\nActive: ${a ? htmlEsc(a.label) : 'none'}`);
  }
  if (cmd === 'list') {
    if (!state.sessions.size) return tgSend(chatId, 'No sessions registered.');
    return tgSend(chatId, [...state.sessions.values()].map(s => `[${htmlEsc(s.label)}]${s.id === state.activeId ? ' <b>active</b>' : ''}`).join('\n'));
  }
  // Phase C (#324): structured per-lane view; sorted by activeId-first then label.
  if (cmd === 'lanes') {
    if (!state.sessions.size) return tgSend(chatId, 'No lanes registered.');
    const rows = [...state.sessions.values()]
      .sort((a, b) => (a.id === state.activeId ? -1 : b.id === state.activeId ? 1 : a.label.localeCompare(b.label)))
      .map(s => `[${htmlEsc(s.label)}]${s.id === state.activeId ? ' <b>active</b>' : ''} branch:${htmlEsc(s.branch || '?')} sse:${(s.sseClients || []).length}`);
    return tgSend(chatId, rows.join('\n'));
  }
  if (cmd === 'help') return tgSend(chatId,
    '/status /list /lanes /help\n' +
    '/cancel [@label] — abort active or named session\n' +
    '/continue [@label] — resume\n' +
    '/dir @label &lt;path&gt; — switch session cwd\n' +
    '/model @label &lt;name&gt; — switch model\n' +
    '/permission-mode @label &lt;mode&gt; — switch perm mode\n' +
    '@label text — route message\nyes/no &lt;id&gt; — verdict');
  if (cmd === 'cancel' || cmd === 'continue') {
    let t = state.sessions.get(state.activeId);
    if (args) {
      // Phase C (#324): accept `@#324` as well as `@324` so TASK-style labels
      // are addressable. Trailing `#` is stripped before findByLabel match.
      const m = args.match(/^@(#?[\w-]+)$/);
      if (!m) return tgSend(chatId, `Usage: /${htmlEsc(cmd)} @&lt;label-prefix&gt;`);
      t = await resolveLane(chatId, m[1].replace(/^#/, ''));
      if (!t) return; // resolveLane already sent the ambiguous / no-match reply
    } else if (!t) {
      return tgSend(chatId, 'No matching session.');
    }
    sendToInbox(t.id, { type: 'command', cmd, from_chat: chatId });
    return tgSend(chatId, `Sent /${htmlEsc(cmd)} to [${htmlEsc(t.label)}]`);
  }
  // Phase C (#324, subsumes #308): pass-through commands to lane inbox.
  if (cmd === 'dir')             return relayLaneCmd(chatId, 'dir',             args, 'path',  '/dir @&lt;label&gt; &lt;path&gt;');
  if (cmd === 'model')           return relayLaneCmd(chatId, 'model',           args, 'model', '/model @&lt;label&gt; &lt;name&gt;');
  if (cmd === 'permission-mode') return relayLaneCmd(chatId, 'permission-mode', args, 'mode',  '/permission-mode @&lt;label&gt; &lt;mode&gt;');
}

async function routeMessage(msg) {
  if (!msg.from || msg.from.id == null) return;       // channel posts / anonymous
  if (msg.chat?.type !== 'private') return;            // refuse groups (MVP)
  if (!isAllowed(msg.from.id)) return;                 // allowlist (fail-closed)
  const chatId = msg.chat.id;
  state.lastChatId = chatId;                           // set only after passing allowlist (M5)
  const text = (msg.text || '').trim();
  if (!text) return;
  const cmdM = text.match(/^\/(\w+)(?:\s+(.*))?$/s);
  if (cmdM) { await handleCmd(chatId, cmdM[1], cmdM[2]?.trim()); return; }
  const vM = text.match(/^\s*(y|yes|n|no)\s+([\w-]+)\s*$/i);
  if (vM) {
    const v = /^y/i.test(vM[1]) ? 'yes' : 'no';
    const rid = vM[2];
    const pm = rid.match(/^([a-z0-9]{6})-([a-km-z]{5})$/);
    if (pm) {
      const t = [...state.sessions.values()].find(s => s.shortId === pm[1]);
      if (t) sendToInbox(t.id, { type: 'verdict', verdict: v, request_id: pm[2] });
      return;
    }
    await tgSend(chatId, `Verdict needs prefixed ID. Use 'yes &lt;short&gt;-&lt;id&gt;' from the request.`);
    return;
  }
  // Issue #407 F2: resolveLane (ambiguity-aware) instead of startsWith (silent first-match).
  const pM = text.match(/^@(#?[\w-]+)\s+(.+)$/s);
  if (pM) {
    const t = await resolveLane(chatId, pM[1].replace(/^#/, ''));
    if (t) sendToInbox(t.id, { type: 'message', content: pM[2], from_chat: chatId });
    return;
  }
  if (!state.activeId || !state.sessions.has(state.activeId)) {
    await tgSend(chatId, 'No active session. Use /list.');
    return;
  }
  sendToInbox(state.activeId, { type: 'message', content: text, from_chat: chatId });
}

module.exports = {
  deriveLabel, sendToInbox, scheduleShutdown,
  findByLabel, resolveLane, parseLanePayload,
  PERMISSION_MODES, COMMAND_VALIDATORS,
  relayLaneCmd, handleCmd, routeMessage,
  SHUTDOWN_GRACE_MS,
};
