#!/usr/bin/env node
'use strict';

// Mercury Channel Router — long-running Telegram bot + IPC server.
// One instance per machine; spawned by mercury-channel-client on first session.

const http = require('http');
const fs   = require('fs');
const os   = require('os');
const path = require('path');

const PORT      = Number(process.env.MERCURY_ROUTER_PORT) || 8788;
const LOCK_FILE = path.join(os.homedir(), '.mercury', 'router.lock');
const MAX_SESS  = 3;
const TAG       = '[mercury-channel-router]';

function acquireLock() {
  try {
    fs.mkdirSync(path.dirname(LOCK_FILE), { recursive: true });
    if (fs.existsSync(LOCK_FILE)) {
      const pid = parseInt(fs.readFileSync(LOCK_FILE, 'utf8').trim(), 10);
      if (pid && pid !== process.pid) {
        try { process.kill(pid, 0); process.stderr.write(`${TAG} already running (pid ${pid})\n`); process.exit(1); }
        catch { /* stale lock */ }
      }
    }
    fs.writeFileSync(LOCK_FILE, String(process.pid));
  } catch (e) { process.stderr.write(`${TAG} lock error: ${e.message}\n`); }
}
const releaseLock = () => { try { fs.unlinkSync(LOCK_FILE); } catch {} };

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

const ALLOWED   = new Set((process.env.MERCURY_TELEGRAM_ALLOWED_USER_IDS || '').split(',').map(s => s.trim()).filter(Boolean));
const isAllowed = id => ALLOWED.size === 0 || ALLOWED.has(String(id));

const sessions = new Map();
let activeId = null, shutdownTimer = null;
const startTs = Date.now();
let lastChatId = null;

function deriveLabel({ project_path = '', branch = '' }) {
  const m1 = branch.match(/^feature\/(?:lane-[\w-]+\/)?TASK-(\d+)-([\w-]+)/);
  if (m1) return `#${m1[1]} ${m1[2]}`.slice(0, 30);
  const m2 = branch.match(/^feature\/([\w-]+)/);
  return (m2 ? m2[1] : path.basename(project_path || process.cwd())).slice(0, 30);
}

function sendToInbox(sid, event) {
  const s = sessions.get(sid); if (!s) return;
  const data = `data: ${JSON.stringify(event)}\n\n`;
  s.sseClients = (s.sseClients || []).filter(r => { try { r.write(data); return true; } catch { return false; } });
}

function scheduleShutdown() {
  if (sessions.size > 0) { clearTimeout(shutdownTimer); shutdownTimer = null; return; }
  if (shutdownTimer) return;
  process.stderr.write(`${TAG} all sessions gone; shutting down in 30s\n`);
  shutdownTimer = setTimeout(() => { releaseLock(); process.exit(0); }, 30000);
}

async function tgSend(chatId, text) {
  if (!bot) return;
  try { await bot.sendMessage(chatId, text.length > 4096 ? text.slice(0, 4090) + '…' : text, { parse_mode: 'HTML' }); }
  catch (e) { process.stderr.write(`${TAG} sendMessage error: ${e.message}\n`); }
}

async function handleCmd(chatId, cmd, args) {
  lastChatId = chatId;
  if (cmd === 'status') {
    const a = sessions.get(activeId);
    return tgSend(chatId, `<b>Mercury Router</b>\nUptime: ${Math.round((Date.now()-startTs)/1000)}s\nSessions: ${sessions.size}/${MAX_SESS}\nActive: ${a ? a.label : 'none'}`);
  }
  if (cmd === 'list') {
    if (!sessions.size) return tgSend(chatId, 'No sessions registered.');
    return tgSend(chatId, [...sessions.values()].map(s => `[${s.label}]${s.id===activeId?' <b>active</b>':''}`).join('\n'));
  }
  if (cmd === 'help') return tgSend(chatId, '/status /list /cancel /continue /help\n@label text — route\nyes/no &lt;id&gt; — verdict');
  if (cmd === 'cancel' || cmd === 'continue') {
    const t = args ? [...sessions.values()].find(s => s.label.includes(args)) : sessions.get(activeId);
    if (!t) return tgSend(chatId, 'No matching session.');
    sendToInbox(t.id, { type: 'command', cmd, from_chat: chatId });
    return tgSend(chatId, `Sent /${cmd} to [${t.label}]`);
  }
}

async function routeMessage(msg) {
  const chatId = msg.chat.id; lastChatId = chatId;
  if (!isAllowed(msg.from?.id)) return;
  const text = (msg.text || '').trim(); if (!text) return;
  const cmdM = text.match(/^\/(\w+)(?:\s+(.*))?$/s);
  if (cmdM) { await handleCmd(chatId, cmdM[1], cmdM[2]?.trim()); return; }
  const vM = text.match(/^\s*(y|yes|n|no)\s+([\w-]+)\s*$/i);
  if (vM) {
    const v = /^y/i.test(vM[1])?'yes':'no', rid = vM[2];
    const pm = rid.match(/^([a-z0-9]{6})-([a-km-z]{5})$/);
    if (pm) { const t=[...sessions.values()].find(s=>s.shortId===pm[1]); if(t) sendToInbox(t.id,{type:'verdict',verdict:v,request_id:pm[2]}); return; }
    for (const s of sessions.values()) sendToInbox(s.id, {type:'verdict',verdict:v,request_id:rid}); return;
  }
  const pM = text.match(/^@([\w-]+)\s+(.+)$/s);
  if (pM) { const t=[...sessions.values()].find(s=>s.label.startsWith(pM[1])); t?sendToInbox(t.id,{type:'message',content:pM[2],from_chat:chatId}):await tgSend(chatId,`No session matching @${pM[1]}`); return; }
  if (!activeId || !sessions.has(activeId)) { await tgSend(chatId, 'No active session. Use /list.'); return; }
  sendToInbox(activeId, { type: 'message', content: text, from_chat: chatId });
}
if (bot) bot.on('message', routeMessage);

const json  = (res, code, obj) => { res.writeHead(code, {'Content-Type':'application/json'}); res.end(JSON.stringify(obj)); };
const bodyOf = req => new Promise((ok, fail) => { let b=''; req.on('data',c=>b+=c); req.on('end',()=>{try{ok(JSON.parse(b||'{}'))}catch(e){fail(e)}}); req.on('error',fail); });

const server = http.createServer(async (req, res) => {
  const url = req.url||'/', m = req.method||'GET';
  if (m==='GET'  && url==='/health')      return json(res,200,{ok:true,sessions:sessions.size,uptime:Date.now()-startTs});
  if (m==='GET'  && url==='/sessions')    return json(res,200,[...sessions.values()].map(({id,label,branch,pid,sseClients})=>({id,label,branch,pid,active:id===activeId,subscribers:(sseClients||[]).length})));
  if (m==='GET'  && url.startsWith('/inbox/')) {
    const sid=url.slice(7); if (!sessions.has(sid)) return json(res,404,{error:'session not found'});
    res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache',Connection:'keep-alive'});
    res.write('data: {"type":"connected"}\n\n');
    const s=sessions.get(sid); s.sseClients=s.sseClients||[]; s.sseClients.push(res);
    req.on('close',()=>{ s.sseClients=(s.sseClients||[]).filter(r=>r!==res); }); return;
  }
  if (m==='POST' && url==='/register') {
    let body; try{body=await bodyOf(req)}catch{return json(res,400,{error:'bad json'});}
    if (sessions.size>=MAX_SESS) return json(res,429,{error:'session limit reached',max:MAX_SESS});
    const {session_id,project_path,branch,pid,short_id}=body;
    if (!session_id) return json(res,400,{error:'session_id required'});
    const label=body.label||deriveLabel({project_path,branch});
    sessions.set(session_id,{id:session_id,label,project_path,branch,pid,shortId:short_id||session_id.slice(0,6),sseClients:[]});
    if (!activeId) activeId=session_id; clearTimeout(shutdownTimer); shutdownTimer=null;
    process.stderr.write(`${TAG} registered ${session_id} [${label}]\n`);
    return json(res,200,{ok:true,label,active:activeId===session_id});
  }
  if (m==='DELETE' && url.startsWith('/register/')) {
    const sid=url.slice(10); sessions.delete(sid);
    if (activeId===sid) activeId=sessions.size>0?sessions.keys().next().value:null;
    process.stderr.write(`${TAG} deregistered ${sid}\n`); scheduleShutdown(); return json(res,200,{ok:true});
  }
  if (m==='POST' && url.startsWith('/take-ownership/')) {
    const sid=url.slice(16); if(!sessions.has(sid)) return json(res,404,{error:'session not found'});
    activeId=sid; return json(res,200,{ok:true});
  }
  if (m==='POST' && url==='/notify') {
    let body; try{body=await bodyOf(req)}catch{return json(res,400,{error:'bad json'});}
    const {severity='info',title='',body:mb='',label:fl}=body;
    const lbl=fl||(sessions.get(activeId)?.label)||'mercury';
    const chatId=lastChatId||(process.env.MERCURY_TELEGRAM_CHAT_ID?Number(process.env.MERCURY_TELEGRAM_CHAT_ID):null);
    if (chatId) await tgSend(chatId,`[${lbl}] <b>${severity.toUpperCase()}: ${title}</b>\n${mb}`);
    return json(res,200,{ok:true});
  }
  if (m==='POST' && url==='/reply') {
    let body; try{body=await bodyOf(req)}catch{return json(res,400,{error:'bad json'});}
    const {chat_id,text,label}=body; if(!chat_id||!text) return json(res,400,{error:'chat_id and text required'});
    const s=[...sessions.values()].find(x=>x.id===body.session_id)||sessions.get(activeId);
    await tgSend(chat_id,`[${label||(s?.label)||'mercury'}] ${text}`); return json(res,200,{ok:true});
  }
  if (m==='POST' && url==='/permission-request') {
    let body; try{body=await bodyOf(req)}catch{return json(res,400,{error:'bad json'});}
    const {session_id,tool_name='',description='',input_preview='',prefixed_request_id=''}=body;
    const chatId=lastChatId||(process.env.MERCURY_TELEGRAM_CHAT_ID?Number(process.env.MERCURY_TELEGRAM_CHAT_ID):null);
    if (chatId) await tgSend(chatId,`Claude wants to run ${tool_name}: ${description}\n\nReply 'yes ${prefixed_request_id}' or 'no ${prefixed_request_id}'`);
    return json(res,200,{ok:true});
  }
  json(res,404,{error:'not found'});
});

acquireLock();
server.listen(PORT,'127.0.0.1',()=>process.stderr.write(`${TAG} IPC server listening on 127.0.0.1:${PORT}\n`));
server.on('error',e=>{process.stderr.write(`${TAG} server error: ${e.message}\n`);releaseLock();process.exit(1);});
process.on('SIGTERM',()=>{releaseLock();process.exit(0);});
process.on('SIGINT', ()=>{releaseLock();process.exit(0);});
