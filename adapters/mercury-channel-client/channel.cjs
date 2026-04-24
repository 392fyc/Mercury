#!/usr/bin/env node
'use strict';

// Mercury Channel Client — MCP server bridging Claude Code sessions to the channel router.
// Loaded by Claude Code via .mcp.json; one instance per session.

const { spawn }  = require('child_process');
const { Server } = require('@modelcontextprotocol/sdk/server/index.js');
const { StdioServerTransport } = require('@modelcontextprotocol/sdk/server/stdio.js');
const { CallToolRequestSchema, ListToolsRequestSchema } = require('@modelcontextprotocol/sdk/types.js');
const path = require('path');
const { execSync } = require('child_process');

const PORT       = Number(process.env.MERCURY_ROUTER_PORT) || 8788;
const ROUTER_CJS = path.join(__dirname, '..', 'mercury-channel-router', 'router.cjs');
const TAG        = '[mercury-channel-client]';

// ── Session identity ──────────────────────────────────────────────────────────
const SESSION_ID = process.env.CLAUDE_SESSION_ID || `cc-${process.pid}-${Date.now().toString(36)}`;
let   branch     = 'unknown';
try { branch = execSync('git branch --show-current', { encoding: 'utf8', stdio: ['pipe','pipe','pipe'] }).trim(); } catch {}

const PROJECT_PATH = process.env.CLAUDE_PROJECT_DIR || process.cwd();

// ── Router IPC helpers ────────────────────────────────────────────────────────
async function routerFetch(path_, opts = {}) {
  const url = `http://127.0.0.1:${PORT}${path_}`;
  return fetch(url, { signal: AbortSignal.timeout(3000), ...opts });
}

async function ensureRouter() {
  try {
    const r = await fetch(`http://127.0.0.1:${PORT}/health`, { signal: AbortSignal.timeout(500) });
    if (r.ok) return;
  } catch {}
  spawn('node', [ROUTER_CJS], { detached: true, stdio: 'ignore', windowsHide: true }).unref();
  for (let i = 0; i < 20; i++) {
    await new Promise(r => setTimeout(r, 250));
    try { const r = await fetch(`http://127.0.0.1:${PORT}/health`); if (r.ok) return; } catch {}
  }
  throw new Error('router did not start within 5s');
}

async function register() {
  const res = await routerFetch('/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: SESSION_ID, project_path: PROJECT_PATH, branch, pid: process.pid }),
  });
  if (res.status === 429) { process.stderr.write(`${TAG} session limit reached; Telegram inactive\n`); return false; }
  return true;
}

async function deregister() {
  try { await routerFetch(`/register/${SESSION_ID}`, { method: 'DELETE' }); } catch {}
}

// ── MCP server ────────────────────────────────────────────────────────────────
const mcp = new Server(
  { name: 'mercury-telegram', version: '0.1.0' },
  {
    capabilities: {
      experimental: { 'claude/channel': {}, 'claude/channel/permission': {} },
      tools: {},
    },
    instructions:
      'Telegram messages arrive as <channel source="mercury-telegram" label="..."> tags. ' +
      'Use the reply tool to respond, passing chat_id from the tag.',
  }
);

// ── Tool: reply ───────────────────────────────────────────────────────────────
mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [{
    name: 'reply',
    description: 'Send a reply to Telegram via the channel router.',
    inputSchema: {
      type: 'object',
      properties: {
        chat_id: { type: 'number', description: 'Telegram chat_id from the channel tag' },
        text:    { type: 'string', description: 'Message text (HTML allowed)' },
      },
      required: ['chat_id', 'text'],
    },
  }],
}));

mcp.setRequestHandler(CallToolRequestSchema, async (req) => {
  if (req.params.name !== 'reply') throw new Error(`Unknown tool: ${req.params.name}`);
  const { chat_id, text } = req.params.arguments || {};
  try {
    await routerFetch('/reply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id, text, session_id: SESSION_ID }),
    });
    // take ownership after responding
    routerFetch(`/take-ownership/${SESSION_ID}`, { method: 'POST' }).catch(() => {});
    return { content: [{ type: 'text', text: 'reply sent' }] };
  } catch (e) {
    return { content: [{ type: 'text', text: `reply failed: ${e.message}` }], isError: true };
  }
});

// ── SSE inbox consumer ────────────────────────────────────────────────────────
let sseActive = true;

async function connectInbox() {
  while (sseActive) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/inbox/${SESSION_ID}`, {
        signal: AbortSignal.timeout(60000),
      });
      if (!res.ok || !res.body) { await new Promise(r => setTimeout(r, 2000)); continue; }
      const reader = res.body.getReader();
      const dec    = new TextDecoder();
      let   buf    = '';
      while (sseActive) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split('\n\n');
        buf = parts.pop();
        for (const part of parts) {
          const line = part.split('\n').find(l => l.startsWith('data:'));
          if (!line) continue;
          try {
            const evt = JSON.parse(line.slice(5).trim());
            if (evt.type === 'message') {
              await mcp.notification({
                method: 'notifications/claude/channel',
                params: {
                  source: 'mercury-telegram',
                  label: SESSION_ID,
                  content: `<channel source="mercury-telegram" chat_id="${evt.from_chat}">${evt.content}</channel>`,
                },
              });
            } else if (evt.type === 'verdict') {
              await mcp.notification({
                method: 'notifications/claude/channel/permission',
                params: { verdict: evt.verdict, request_id: evt.request_id },
              });
            }
          } catch {}
        }
      }
    } catch {
      if (!sseActive) break;
      // reconnect: re-ensure router then retry
      try { await ensureRouter(); } catch {}
      await new Promise(r => setTimeout(r, 2000));
    }
  }
}

// ── Graceful exit ─────────────────────────────────────────────────────────────
async function shutdown() {
  sseActive = false;
  await deregister();
  process.exit(0);
}
process.on('SIGTERM', shutdown);
process.on('SIGINT',  shutdown);
process.on('exit',    () => { try { require('child_process').execSync(`node -e "require('node:http').request({hostname:'127.0.0.1',port:${PORT},path:'/register/${SESSION_ID}',method:'DELETE'}).end()"`, { timeout: 2000 }); } catch {} });

// ── Main ──────────────────────────────────────────────────────────────────────
(async () => {
  try {
    await ensureRouter();
    const registered = await register();
    if (registered) connectInbox().catch(e => process.stderr.write(`${TAG} inbox error: ${e.message}\n`));
  } catch (e) {
    process.stderr.write(`${TAG} startup error: ${e.message}\n`);
  }
  const transport = new StdioServerTransport();
  await mcp.connect(transport);
})();
