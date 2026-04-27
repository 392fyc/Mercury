#!/usr/bin/env node
'use strict';

// Mercury Channel Router — long-running Telegram bot + IPC server.
// One instance per machine; spawned by mercury-channel-client on first session.

const http   = require('http');
const fs     = require('fs');
const os     = require('os');
const path   = require('path');
const crypto = require('crypto');

const PORT       = Number(process.env.MERCURY_ROUTER_PORT) || 8788;
const LOCK_FILE  = path.join(os.homedir(), '.mercury', 'router.lock');
const TOKEN_FILE = path.join(os.homedir(), '.mercury', 'router.token');
// Phase C (#324): bump default 3→5 to match feedback_lane_protocol.md HARD-CAP.
// MERCURY_ROUTER_MAX_SESS env override is floored to a positive integer; non-finite
// or non-positive values fall back to default 5 so the cap is always integer.
const MAX_SESS_RAW = Number(process.env.MERCURY_ROUTER_MAX_SESS);
const MAX_SESS   = Number.isFinite(MAX_SESS_RAW) && MAX_SESS_RAW >= 1 ? Math.floor(MAX_SESS_RAW) : 5;
const TAG        = '[mercury-channel-router]';

// IPC auth token — written to TOKEN_FILE after server.listen succeeds
const TOKEN = crypto.randomBytes(16).toString('hex');
const writeToken  = () => { try { fs.mkdirSync(path.dirname(TOKEN_FILE),{recursive:true}); fs.writeFileSync(TOKEN_FILE,TOKEN,{mode:0o600}); } catch(e){process.stderr.write(`${TAG} token write error: ${e.message}\n`);} };
const cleanupToken = () => { try { fs.unlinkSync(TOKEN_FILE); } catch {} };

// Lock file — atomic O_CREAT|O_EXCL; fail-closed on EADDRINUSE (lock not yet held)
function acquireLock() {
  fs.mkdirSync(path.dirname(LOCK_FILE), { recursive: true });
  for (let i = 0; i < 3; i++) {
    try { const fd=fs.openSync(LOCK_FILE,'wx'); fs.writeSync(fd,String(process.pid)); fs.closeSync(fd); return; }
    catch (e) {
      if (e.code !== 'EEXIST') { process.stderr.write(`${TAG} lock error: ${e.message}\n`); return; }
      try {
        const pid = parseInt(fs.readFileSync(LOCK_FILE,'utf8').trim(),10);
        if (pid && pid !== process.pid) {
          try { process.kill(pid,0); process.stderr.write(`${TAG} already running (pid ${pid})\n`); process.exit(1); }
          catch (e2) { if (e2.code === 'EPERM') { process.stderr.write(`${TAG} pid ${pid} exists (no permission); aborting\n`); process.exit(1); } fs.unlinkSync(LOCK_FILE); } // ESRCH/other → stale, retry
        } else { fs.unlinkSync(LOCK_FILE); }
      } catch { fs.unlinkSync(LOCK_FILE); }
    }
  }
}
function releaseLock() { try { const pid=parseInt(fs.readFileSync(LOCK_FILE,'utf8').trim(),10); if(pid===process.pid)fs.unlinkSync(LOCK_FILE); } catch {} }

// Telegram bot
let bot = null;
const BOT_TOKEN = process.env.MERCURY_TELEGRAM_BOT_TOKEN;
if (!process.env.MERCURY_NOTIFY_DISABLED && BOT_TOKEN) {
  try {
    const TelegramBot = require('node-telegram-bot-api');
    bot = new TelegramBot(BOT_TOKEN, { polling: true });
    bot.on('polling_error', e => process.stderr.write(`${TAG} polling error: ${e.message}\n`));
    process.stderr.write(`${TAG} Telegram polling started\n`);
  } catch (e) { process.stderr.write(`${TAG} Telegram init failed: ${e.message}\n`); }
} else if (!BOT_TOKEN) {
  process.stderr.write(`${TAG} WARNING: MERCURY_TELEGRAM_BOT_TOKEN not set; Telegram disabled\n`);
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
  const s=sessions.get(sid); if(!s) return;
  const data=`data: ${JSON.stringify(event)}\n\n`;
  s.sseClients=(s.sseClients||[]).filter(r=>{try{r.write(data);return true;}catch{return false;}});
}

function scheduleShutdown() {
  if (sessions.size>0){clearTimeout(shutdownTimer);shutdownTimer=null;return;}
  if (shutdownTimer) return;
  process.stderr.write(`${TAG} all sessions gone; shutting down in 30s\n`);
  shutdownTimer=setTimeout(()=>{releaseLock();cleanupToken();process.exit(0);},30000);
}

async function tgSend(chatId, text) {
  if (!bot) return;
  const payload = text.length>4096?text.slice(0,4090)+'…':text;
  for (let attempt=0; attempt<2; attempt++) {
    try { await bot.sendMessage(chatId, payload, {parse_mode:'HTML'}); return; }
    catch (e) {
      const ra = Number(e?.response?.body?.parameters?.retry_after);
      if (attempt===0 && Number.isFinite(ra) && ra>0 && ra<=5) { await new Promise(r=>setTimeout(r,ra*1000)); continue; }
      process.stderr.write(`${TAG} sendMessage error (attempt ${attempt+1}): ${e.message}\n`); return;
    }
  }
}

// Phase C (#324): find session whose label matches the given prefix.
// Mercury labels include `#324 director-surface` (TASK branches) and
// `lane-main/foo` (lane branches). Match plain prefix OR the label with a
// leading `#` stripped, so `@324` finds `#324 director`. Used by
// /cancel /continue /dir /model /permission-mode.
const findByLabel = prefix => [...sessions.values()].find(s =>
  s.label.startsWith(prefix) || s.label.replace(/^#/, '').startsWith(prefix));

// Phase C: parse `@<lane> <payload>` from cmd args. Returns {lane, payload} or null.
// Lane token allows `#`, word chars and `-` so users can address `@#324` directly
// in addition to the bare numeric form `@324`.
const parseLanePayload = args => {
  if (!args) return null;
  const m = args.match(/^@(#?[\w-]+)\s+(.+)$/s);
  return m ? { lane: m[1].replace(/^#/, ''), payload: m[2].trim() } : null;
};

// Phase C: relay a command to a lane's inbox; replies to chat with status.
// `usage` is the help text shown when args parse fails.
async function relayLaneCmd(chatId, cmd, args, payloadKey, usage) {
  const lp = parseLanePayload(args);
  if (!lp || !lp.payload) return tgSend(chatId, `Usage: ${usage}`);
  const t = findByLabel(lp.lane);
  if (!t) return tgSend(chatId, `No session matching @${htmlEsc(lp.lane)}`);
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
      t = findByLabel(m[1].replace(/^#/, ''));
    }
    if (!t) return tgSend(chatId,'No matching session.');
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
if (bot) bot.on('message',routeMessage);

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
    const s=sessions.get(sid);s.sseClients=s.sseClients||[];s.sseClients.push(res);
    req.on('close',()=>{s.sseClients=(s.sseClients||[]).filter(r=>r!==res);});return;
  }
  if (m==='POST'&&url==='/register'){
    let body;try{body=await bodyOf(req)}catch{return json(res,400,{error:'bad json'});}
    if (sessions.size>=MAX_SESS) return json(res,429,{error:'session limit reached',max:MAX_SESS});
    const {session_id,project_path,branch,pid,short_id}=body;
    if (!session_id) return json(res,400,{error:'session_id required'});
    const label=body.label||deriveLabel({project_path,branch});
    sessions.set(session_id,{id:session_id,label,project_path,branch,pid,shortId:short_id||session_id.slice(0,6),sseClients:[]});
    if (!activeId)activeId=session_id;clearTimeout(shutdownTimer);shutdownTimer=null;
    process.stderr.write(`${TAG} registered ${session_id} [${label}]\n`);
    return json(res,200,{ok:true,label,active:activeId===session_id});
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

// Startup: listen first, then acquire lock + write token (avoids holding lock if port busy)
server.listen(PORT,'127.0.0.1',()=>{
  process.stderr.write(`${TAG} IPC server listening on 127.0.0.1:${PORT}\n`);
  acquireLock(); writeToken();
});
server.on('error',e=>{process.stderr.write(`${TAG} server error: ${e.message}\n`);process.exit(1);});
// do NOT releaseLock on server error — lock may not have been acquired yet

const cleanup=()=>{releaseLock();cleanupToken();};
process.on('SIGTERM',()=>{cleanup();process.exit(0);});
process.on('SIGINT', ()=>{cleanup();process.exit(0);});
