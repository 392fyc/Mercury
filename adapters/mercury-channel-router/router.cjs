#!/usr/bin/env node
'use strict';

// Mercury Channel Router — long-running Telegram bot + IPC server.
// One instance per machine; spawned by mercury-channel-client on first session.

const http   = require('http');
const fs     = require('fs');
const os     = require('os');
const path   = require('path');
const crypto = require('crypto');
const { truncateForTelegram } = require('./lib/truncate.cjs');
const { isEnvTruthy } = require('./lib/env.cjs');

const PORT       = Number(process.env.MERCURY_ROUTER_PORT) || 8788;
const LOCK_FILE  = path.join(os.homedir(), '.mercury', 'router.lock');
const TOKEN_FILE = path.join(os.homedir(), '.mercury', 'router.token');
// Phase C (#324): bump default 3→5 to match feedback_lane_protocol.md HARD-CAP.
// MERCURY_ROUTER_MAX_SESS env override is floored to a positive integer; non-finite
// or non-positive values fall back to default 5 so the cap is always integer.
const MAX_SESS_RAW = Number(process.env.MERCURY_ROUTER_MAX_SESS);
const MAX_SESS   = Number.isFinite(MAX_SESS_RAW) && MAX_SESS_RAW >= 1 ? Math.floor(MAX_SESS_RAW) : 5;
const TAG        = '[mercury-channel-router]';

// Issue #304: extract magic numbers to named constants near top of file.
const SHUTDOWN_GRACE_MS         = 30000; // delay before exit after last session deregisters
const LOCK_ACQUIRE_RETRIES      = 3;     // attempts to claim ~/.mercury/router.lock
const TG_SEND_MAX_RETRIES       = 2;     // tgSend retry budget
const TG_SEND_RETRY_AFTER_MAX_S = 5;     // max seconds to honor Telegram retry_after hint

// IPC auth token — written to TOKEN_FILE after server.listen succeeds
const TOKEN = crypto.randomBytes(16).toString('hex');
const writeToken  = () => { try { fs.mkdirSync(path.dirname(TOKEN_FILE),{recursive:true}); fs.writeFileSync(TOKEN_FILE,TOKEN,{mode:0o600}); } catch(e){process.stderr.write(`${TAG} token write error: ${e.message}\n`);} };
const cleanupToken = () => { try { fs.unlinkSync(TOKEN_FILE); } catch {} };

// Lock file — atomic O_CREAT|O_EXCL; fail-closed on EEXIST (lock contention,
// not socket EADDRINUSE — Copilot iter-1 C2 comment-correctness finding).
// Issue #304 nit 4: returns true on success, false on non-fatal failure paths
// (e.g. filesystem error other than EEXIST, or retry exhaustion). Callers
// MUST gate `writeToken()` / `initBot()` on the boolean — otherwise Telegram
// polling could start without this process actually owning the singleton
// lock, which would re-open the 409 race the rest of the PR closes.
// Other failure paths (live PID owns the lock, EPERM probing) still call
// `process.exit(1)` directly; this function only returns false on paths
// that the caller should treat as "abort startup, do not poll".
// Stale-lock unlinks are guarded by `safeUnlinkLock()` — Copilot iter-1 C1:
// without the guard a bare `fs.unlinkSync(LOCK_FILE)` could throw on ENOENT
// (TOCTOU race against another router cleaning the same stale lock) or
// EPERM/EACCES (Windows file lock from a zombie router), which would
// propagate out past the new `acquireLock()` boolean contract and crash
// the listen callback instead of falling through to the boolean-gated
// startup abort.
const safeUnlinkLock = () => {
  try { fs.unlinkSync(LOCK_FILE); }
  catch (e) {
    if (e && e.code !== 'ENOENT') {
      process.stderr.write(`${TAG} stale-lock unlink failed: ${e.message}\n`);
    }
  }
};
function acquireLock() {
  fs.mkdirSync(path.dirname(LOCK_FILE), { recursive: true });
  for (let i = 0; i < LOCK_ACQUIRE_RETRIES; i++) {
    try { const fd=fs.openSync(LOCK_FILE,'wx'); fs.writeSync(fd,String(process.pid)); fs.closeSync(fd); return true; }
    catch (e) {
      if (e.code !== 'EEXIST') { process.stderr.write(`${TAG} lock error: ${e.message}\n`); return false; }
      try {
        const pid = parseInt(fs.readFileSync(LOCK_FILE,'utf8').trim(),10);
        if (pid && pid !== process.pid) {
          try { process.kill(pid,0); process.stderr.write(`${TAG} already running (pid ${pid})\n`); process.exit(1); }
          catch (e2) { if (e2.code === 'EPERM') { process.stderr.write(`${TAG} pid ${pid} exists (no permission); aborting\n`); process.exit(1); } safeUnlinkLock(); } // ESRCH/other → stale, retry
        } else { safeUnlinkLock(); }
      } catch { safeUnlinkLock(); }
    }
  }
  process.stderr.write(`${TAG} lock acquisition failed after ${LOCK_ACQUIRE_RETRIES} retries\n`);
  return false;
}
function releaseLock() { try { const pid=parseInt(fs.readFileSync(LOCK_FILE,'utf8').trim(),10); if(pid===process.pid)fs.unlinkSync(LOCK_FILE); } catch {} }

// Telegram bot — instance set by initBot() AFTER acquireLock() succeeds.
let bot = null;
const BOT_TOKEN = process.env.MERCURY_TELEGRAM_BOT_TOKEN;
// Issue #304 nit 4: bot polling is started by initBot() inside the
// server.listen callback, AFTER acquireLock() establishes that this process
// owns the singleton lock. Previously the `new TelegramBot({polling:true})`
// call lived at module load, so a second router started during startup would
// briefly poll the same getUpdates queue and hit a Telegram 409 conflict in
// the window between module init and the server.listen EADDRINUSE handler.
// Issue #298: strict truthy check — '0', 'false', 'no', 'off', '', unset → enabled.
function initBot() {
  if (isEnvTruthy(process.env.MERCURY_NOTIFY_DISABLED)) return;
  if (!BOT_TOKEN) {
    process.stderr.write(`${TAG} WARNING: MERCURY_TELEGRAM_BOT_TOKEN not set; Telegram disabled\n`);
    return;
  }
  try {
    const TelegramBot = require('node-telegram-bot-api');
    bot = new TelegramBot(BOT_TOKEN, { polling: true });
    bot.on('polling_error', e => process.stderr.write(`${TAG} polling error: ${e.message}\n`));
    bot.on('message', routeMessage);
    process.stderr.write(`${TAG} Telegram polling started\n`);
  } catch (e) { process.stderr.write(`${TAG} Telegram init failed: ${e.message}\n`); }
}

// Allowlist — fail-closed: empty set blocks all inbound messages
const ALLOWED   = new Set((process.env.MERCURY_TELEGRAM_ALLOWED_USER_IDS||'').split(',').map(s=>s.trim()).filter(Boolean));
const isAllowed = id => ALLOWED.has(String(id));
if (BOT_TOKEN && ALLOWED.size === 0)
  process.stderr.write(`${TAG} WARNING: ALLOWED user IDs empty; ALL inbound Telegram messages will be dropped. Set MERCURY_TELEGRAM_ALLOWED_USER_IDS to enable.\n`);

// HTML escape helper — applied to all user-controlled interpolations in tgSend calls
const htmlEsc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

const sessions = new Map();
let activeId = null, shutdownTimer = null;
const startTs = Date.now();
let lastChatId = null;

function deriveLabel({ project_path='', branch='' }) {
  const m1 = branch.match(/^feature\/(?:lane-[\w-]+\/)?TASK-(\d+)-([\w-]+)/);
  if (m1) return `#${m1[1]} ${m1[2]}`.slice(0,30);
  const m2 = branch.match(/^feature\/([\w-]+)/);
  return (m2?m2[1]:path.basename(project_path||process.cwd())).slice(0,30);
}

function sendToInbox(sid, event) {
  const s=sessions.get(sid); if(!s||!s.sseClients) return;
  const data=`data: ${JSON.stringify(event)}\n\n`;
  // Issue #297: mutate in place (splice) instead of reassigning a filtered
  // copy. /register upsert preserves the sseClients array reference, and the
  // /inbox close handler holds a reference to it; reassigning here would
  // detach Map entry from the array the close handler watches.
  for (let i = s.sseClients.length - 1; i >= 0; i--) {
    try { s.sseClients[i].write(data); }
    catch { s.sseClients.splice(i, 1); }
  }
}

function scheduleShutdown() {
  if (sessions.size>0){clearTimeout(shutdownTimer);shutdownTimer=null;return;}
  if (shutdownTimer) return;
  process.stderr.write(`${TAG} all sessions gone; shutting down in ${SHUTDOWN_GRACE_MS}ms\n`);
  shutdownTimer=setTimeout(()=>{releaseLock();cleanupToken();process.exit(0);},SHUTDOWN_GRACE_MS);
}

async function tgSend(chatId, text) {
  if (!bot) return;
  // Coerce at the API boundary so non-string callers (defensive) and
  // null/undefined become an empty string; skip empty payloads instead of
  // letting Telegram 400 on them and retrying.
  const payload = truncateForTelegram(String(text ?? ''));
  if (!payload) return;
  for (let attempt=0; attempt<TG_SEND_MAX_RETRIES; attempt++) {
    try { await bot.sendMessage(chatId, payload, {parse_mode:'HTML'}); return; }
    catch (e) {
      const ra = Number(e?.response?.body?.parameters?.retry_after);
      if (attempt===0 && Number.isFinite(ra) && ra>0 && ra<=TG_SEND_RETRY_AFTER_MAX_S) { await new Promise(r=>setTimeout(r,ra*1000)); continue; }
      process.stderr.write(`${TAG} sendMessage error (attempt ${attempt+1}): ${e.message}\n`); return;
    }
  }
}

// Phase C (#324): find session whose label matches the given prefix.
// Resolution order is strict and never auto-picks among ambiguous candidates:
//   1. exact label equality wins outright
//   2. exactly one prefix match (with optional leading `#` stripped) wins
//   3. multiple prefix matches → ambiguous, caller must surface candidates so
//      the user retries with a longer disambiguating prefix
//   4. zero matches → undefined match
// Return shape:
//   { match: <session> }  — single resolved target
//   { match: undefined }  — no candidate
//   { ambiguous: [labels] } — caller emits "ambiguous, candidates: ..." reply
const findByLabel = prefix => {
  const sess = [...sessions.values()];
  const exact = sess.find(s => s.label === prefix);
  if (exact) return { match: exact };
  const matches = sess.filter(s =>
    s.label.startsWith(prefix) || s.label.replace(/^#/, '').startsWith(prefix));
  if (matches.length === 0) return { match: undefined };
  if (matches.length === 1) return { match: matches[0] };
  return { ambiguous: matches.map(s => s.label) };
};

// Helper: collapse a findByLabel result + a `none-match` reply into a single
// "match-or-tgSend" branch used by every lane-targeted command. Returns
// the matched session, or null after sending the appropriate user-facing reply.
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

// Phase C: payload validators per cmd. Reject anything we can't safely deliver
// before crossing the trust boundary into the lane session. Allowlist already
// gates upstream (routeMessage::isAllowed), but defense-in-depth keeps a typo
// from a trusted user out of `cwd` / `model` / `permission-mode` commands.
// `dir`: filesystem-safe chars only, no `..` (path traversal).
// `model`: alphanumeric + `.` `-` `_`, max 64 (covers `claude-opus-4-7`, `sonnet`, etc.).
// `permission-mode`: enum drawn from Claude Code's documented modes.
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
  if (cmd==='status') {
    const a=sessions.get(activeId);
    return tgSend(chatId,`<b>Mercury Router</b>\nUptime: ${Math.round((Date.now()-startTs)/1000)}s\nSessions: ${sessions.size}/${MAX_SESS}\nActive: ${a?htmlEsc(a.label):'none'}`);
  }
  if (cmd==='list') {
    if (!sessions.size) return tgSend(chatId,'No sessions registered.');
    return tgSend(chatId,[...sessions.values()].map(s=>`[${htmlEsc(s.label)}]${s.id===activeId?' <b>active</b>':''}`).join('\n'));
  }
  // Phase C (#324): structured per-lane view; sorted by activeId-first then label.
  if (cmd==='lanes') {
    if (!sessions.size) return tgSend(chatId,'No lanes registered.');
    const rows=[...sessions.values()]
      .sort((a,b)=>(a.id===activeId?-1:b.id===activeId?1:a.label.localeCompare(b.label)))
      .map(s=>`[${htmlEsc(s.label)}]${s.id===activeId?' <b>active</b>':''} branch:${htmlEsc(s.branch||'?')} sse:${(s.sseClients||[]).length}`);
    return tgSend(chatId,rows.join('\n'));
  }
  if (cmd==='help') return tgSend(chatId,
    '/status /list /lanes /help\n'+
    '/cancel [@label] — abort active or named session\n'+
    '/continue [@label] — resume\n'+
    '/dir @label &lt;path&gt; — switch session cwd\n'+
    '/model @label &lt;name&gt; — switch model\n'+
    '/permission-mode @label &lt;mode&gt; — switch perm mode\n'+
    '@label text — route message\nyes/no &lt;id&gt; — verdict');
  if (cmd==='cancel'||cmd==='continue') {
    let t = sessions.get(activeId);
    if (args) {
      // Phase C (#324): accept `@#324` as well as `@324` so TASK-style labels
      // are addressable. Trailing `#` is stripped before findByLabel match.
      const m = args.match(/^@(#?[\w-]+)$/);
      if (!m) return tgSend(chatId,`Usage: /${htmlEsc(cmd)} @&lt;label-prefix&gt;`);
      t = await resolveLane(chatId, m[1].replace(/^#/, ''));
      if (!t) return; // resolveLane already sent the ambiguous / no-match reply
    } else if (!t) {
      return tgSend(chatId, 'No matching session.');
    }
    sendToInbox(t.id,{type:'command',cmd,from_chat:chatId});
    return tgSend(chatId,`Sent /${htmlEsc(cmd)} to [${htmlEsc(t.label)}]`);
  }
  // Phase C (#324, subsumes #308): pass-through commands to lane inbox.
  if (cmd==='dir')             return relayLaneCmd(chatId,'dir',            args,'path', '/dir @&lt;label&gt; &lt;path&gt;');
  if (cmd==='model')           return relayLaneCmd(chatId,'model',          args,'model','/model @&lt;label&gt; &lt;name&gt;');
  if (cmd==='permission-mode') return relayLaneCmd(chatId,'permission-mode',args,'mode', '/permission-mode @&lt;label&gt; &lt;mode&gt;');
}

async function routeMessage(msg) {
  if (!msg.from||msg.from.id==null) return;       // channel posts / anonymous
  if (msg.chat?.type!=='private') return;          // refuse groups (MVP)
  if (!isAllowed(msg.from.id)) return;             // allowlist (fail-closed)
  const chatId=msg.chat.id;
  lastChatId=chatId;                               // set only after passing allowlist (M5)
  const text=(msg.text||'').trim(); if(!text) return;
  const cmdM=text.match(/^\/(\w+)(?:\s+(.*))?$/s);
  if (cmdM){await handleCmd(chatId,cmdM[1],cmdM[2]?.trim());return;}
  const vM=text.match(/^\s*(y|yes|n|no)\s+([\w-]+)\s*$/i);
  if (vM) {
    const v=/^y/i.test(vM[1])?'yes':'no', rid=vM[2];
    const pm=rid.match(/^([a-z0-9]{6})-([a-km-z]{5})$/);
    if (pm){const t=[...sessions.values()].find(s=>s.shortId===pm[1]);if(t)sendToInbox(t.id,{type:'verdict',verdict:v,request_id:pm[2]});return;}
    await tgSend(chatId,`Verdict needs prefixed ID. Use 'yes &lt;short&gt;-&lt;id&gt;' from the request.`);
    return;
  }
  const pM=text.match(/^@([\w-]+)\s+(.+)$/s);
  if (pM){const t=[...sessions.values()].find(s=>s.label.startsWith(pM[1]));t?sendToInbox(t.id,{type:'message',content:pM[2],from_chat:chatId}):await tgSend(chatId,`No session matching @${htmlEsc(pM[1])}`);return;}
  if (!activeId||!sessions.has(activeId)){await tgSend(chatId,'No active session. Use /list.');return;}
  sendToInbox(activeId,{type:'message',content:text,from_chat:chatId});
}
// Issue #304 nit 4: bot.on('message', routeMessage) moved into initBot() so
// it stays atomic with the deferred bot creation. No top-level wiring here.

const json   = (res,code,obj)=>{res.writeHead(code,{'Content-Type':'application/json'});res.end(JSON.stringify(obj));};
const bodyOf = req=>new Promise((ok,fail)=>{let b='';req.on('data',c=>b+=c);req.on('end',()=>{try{ok(JSON.parse(b||'{}'))}catch(e){fail(e)}});req.on('error',fail);});

const server = http.createServer(async (req,res)=>{
  const url=req.url||'/',m=req.method||'GET';
  if (m==='GET'&&url==='/health') return json(res,200,{ok:true,sessions:sessions.size,uptime:Date.now()-startTs});
  if (req.headers.authorization!==`Bearer ${TOKEN}`) return json(res,401,{error:'unauthorized'});
  if (m==='GET'&&url==='/sessions') return json(res,200,[...sessions.values()].map(({id,label,branch,pid,sseClients})=>({id,label,branch,pid,active:id===activeId,subscribers:(sseClients||[]).length})));
  if (m==='GET'&&url.startsWith('/inbox/')){
    const sid=url.slice(7);if(!sessions.has(sid))return json(res,404,{error:'session not found'});
    res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache',Connection:'keep-alive'});
    res.write('data: {"type":"connected"}\n\n');
    // Issue #297: mutate the underlying array in place (splice) rather than
    // reassigning a filtered copy, so that the disconnect cleanup survives a
    // subsequent /register upsert. After upsert, sessions.get(sid).sseClients
    // is the SAME array reference (preserved from existing). Reassigning
    // `s.sseClients = ...filter()` would only update the captured (now-stale)
    // session object's property, leaving the live Map entry holding a dead
    // ServerResponse forever.
    const s=sessions.get(sid);s.sseClients=s.sseClients||[];s.sseClients.push(res);
    const sseArr=s.sseClients;
    req.on('close',()=>{const i=sseArr.indexOf(res);if(i!==-1)sseArr.splice(i,1);});return;
  }
  if (m==='POST'&&url==='/register'){
    let body;try{body=await bodyOf(req)}catch{return json(res,400,{error:'bad json'});}
    // Argus #297 iter-1: session_id is an externally-supplied identifier that
    // flows into stderr logs and Map keys. Restrict to a controlled charset
    // and length to defend against log injection and unbounded memory growth
    // from malformed bodies. Accepts the existing client format
    // `cc-<pid>-<base36>` plus arbitrary CLAUDE_SESSION_ID values from
    // upstream — those are dash-separated lowercase hex (uuid-like).
    const session_id = String(body.session_id || '').trim();
    if (!/^[A-Za-z0-9._:-]{1,128}$/.test(session_id)) {
      return json(res,400,{error:'invalid session_id'});
    }
    const {project_path,branch,pid,short_id}=body;
    // Issue #297: /register is an upsert. Existing session_id → update fields in
    // place and preserve `sseClients` so the live SSE connection from
    // connectInbox() is not orphaned. The MAX_SESS cap only applies to NEW
    // sessions; updating an existing session must not be rejected.
    const existing = sessions.get(session_id);
    if (!existing && sessions.size>=MAX_SESS) return json(res,429,{error:'session limit reached',max:MAX_SESS});
    const label=body.label||deriveLabel({project_path,branch});
    sessions.set(session_id,{
      id:session_id,label,project_path,branch,pid,
      shortId:short_id||session_id.slice(0,6),
      sseClients: existing ? existing.sseClients : [],
    });
    if (!activeId)activeId=session_id;clearTimeout(shutdownTimer);shutdownTimer=null;
    process.stderr.write(`${TAG} ${existing ? 'updated' : 'registered'} ${session_id} [${label}]\n`);
    return json(res,200,{ok:true,label,active:activeId===session_id,updated:!!existing});
  }
  if (m==='DELETE'&&url.startsWith('/register/')){
    const sid=url.slice(10);sessions.delete(sid);
    if (activeId===sid)activeId=sessions.size>0?sessions.keys().next().value:null;
    process.stderr.write(`${TAG} deregistered ${sid}\n`);scheduleShutdown();return json(res,200,{ok:true});
  }
  if (m==='POST'&&url.startsWith('/take-ownership/')){
    const sid=url.slice(16);if(!sessions.has(sid))return json(res,404,{error:'session not found'});
    activeId=sid;return json(res,200,{ok:true});
  }
  if (m==='POST'&&url==='/notify'){
    let body;try{body=await bodyOf(req)}catch{return json(res,400,{error:'bad json'});}
    const {severity='info',title='',body:mb='',label:fl}=body;
    const lbl=fl||(sessions.get(activeId)?.label)||'mercury';
    const chatId=lastChatId||(process.env.MERCURY_TELEGRAM_CHAT_ID?Number(process.env.MERCURY_TELEGRAM_CHAT_ID):null);
    if (chatId) await tgSend(chatId,`[${htmlEsc(lbl)}] <b>${htmlEsc(String(severity).toUpperCase())}: ${htmlEsc(title)}</b>\n${htmlEsc(mb)}`);
    return json(res,200,{ok:true});
  }
  if (m==='POST'&&url==='/reply'){
    let body;try{body=await bodyOf(req)}catch{return json(res,400,{error:'bad json'});}
    const {chat_id,text,label}=body;if(!chat_id||!text)return json(res,400,{error:'chat_id and text required'});
    const s=[...sessions.values()].find(x=>x.id===body.session_id)||sessions.get(activeId);
    await tgSend(chat_id,`[${htmlEsc(label||(s?.label)||'mercury')}] ${htmlEsc(text)}`);return json(res,200,{ok:true});
  }
  if (m==='POST'&&url==='/permission-request'){
    let body;try{body=await bodyOf(req)}catch{return json(res,400,{error:'bad json'});}
    const {tool_name='',description='',prefixed_request_id=''}=body;
    const chatId=lastChatId||(process.env.MERCURY_TELEGRAM_CHAT_ID?Number(process.env.MERCURY_TELEGRAM_CHAT_ID):null);
    if (chatId) await tgSend(chatId,`Claude wants to run ${htmlEsc(tool_name)}: ${htmlEsc(description)}\n\nReply 'yes ${htmlEsc(prefixed_request_id)}' or 'no ${htmlEsc(prefixed_request_id)}'`);
    return json(res,200,{ok:true});
  }
  json(res,404,{error:'not found'});
});

// Startup: listen first, then acquire lock + write token + start Telegram polling.
// Issue #304 nit 4: bot init is sequenced AFTER acquireLock() so a second
// router process never starts polling Telegram (and never wins/loses a 409
// race against the running instance) — its server.listen EADDRINUSE handler
// fires first and exits before initBot() runs. AND on this (lock-owning)
// process we gate writeToken()+initBot() on acquireLock() returning true,
// so a filesystem-level lock failure aborts startup rather than silently
// polling Telegram without singleton ownership (Codex sync-audit R2 M1).
server.listen(PORT,'127.0.0.1',()=>{
  process.stderr.write(`${TAG} IPC server listening on 127.0.0.1:${PORT}\n`);
  if (!acquireLock()) { process.stderr.write(`${TAG} aborting startup; Telegram polling not started\n`); process.exit(1); }
  writeToken();
  initBot();
});
server.on('error',e=>{process.stderr.write(`${TAG} server error: ${e.message}\n`);process.exit(1);});
// do NOT releaseLock on server error — lock may not have been acquired yet

const cleanup=()=>{releaseLock();cleanupToken();};
process.on('SIGTERM',()=>{cleanup();process.exit(0);});
process.on('SIGINT', ()=>{cleanup();process.exit(0);});
