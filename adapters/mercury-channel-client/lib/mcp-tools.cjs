'use strict';

// MCP server + reply tool + outbound permission_request relay. #303 split
// from channel.cjs. Exports the configured `mcp` instance for channel.cjs to
// connect to a StdioServerTransport in the main IIFE.

const { Server } = require('@modelcontextprotocol/sdk/server/index.js');
const { CallToolRequestSchema, ListToolsRequestSchema } = require('@modelcontextprotocol/sdk/types.js');

const state = require('./state.cjs');
const { routerFetch } = require('./router-bootstrap.cjs');

const { TAG, SESSION_ID, SESSION_SHORT } = state;

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

// Tool: reply. Issue #407 Codex Low: inputSchema mirrors the router-side
// validation (lib/ipc.cjs `/reply` — chat_id integer, text non-empty trim) so
// callers fail at schema-validation time instead of round-tripping a 400.
mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [{
    name: 'reply',
    description: 'Send a reply to Telegram via the channel router.',
    inputSchema: {
      type: 'object',
      properties: {
        chat_id: { type: 'integer', description: 'Telegram chat_id from the channel tag' },
        text:    { type: 'string', minLength: 1, pattern: '\\S', description: 'Message text (HTML allowed; must contain non-whitespace)' },
      },
      required: ['chat_id', 'text'],
    },
  }],
}));

// Issue #407 Codex Medium: the router can now reject /reply with 400/413
// (F1 + F3). Gate take-ownership on res.ok so a rejected reply does not
// silently flip the active lane; surface the router's JSON error to MCP.
mcp.setRequestHandler(CallToolRequestSchema, async (req) => {
  if (req.params.name !== 'reply') throw new Error(`Unknown tool: ${req.params.name}`);
  const { chat_id, text } = req.params.arguments || {};
  try {
    const res = await routerFetch('/reply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id, text, session_id: SESSION_ID }),
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { const j = await res.json(); if (j && j.error) detail = `HTTP ${res.status}: ${j.error}`; } catch {}
      return { content: [{ type: 'text', text: `reply rejected by router: ${detail}` }], isError: true };
    }
    routerFetch(`/take-ownership/${SESSION_ID}`, { method: 'POST' }).catch(() => {});
    return { content: [{ type: 'text', text: 'reply sent' }] };
  } catch (e) {
    return { content: [{ type: 'text', text: `reply failed: ${e.message}` }], isError: true };
  }
});

// Outbound permission_request relay (ADR §5.2 step 6 + §7.6)
// Issue #407 F4: `input_preview` is intentionally NOT forwarded. The router's
// /permission-request handler (lib/ipc.cjs) only reads tool_name + description
// + prefixed_request_id; the field used to carry raw tool input that could
// include secrets (API keys, file contents) and never reached Telegram. Until
// the router actually needs it, dropping it shrinks the IPC surface area and
// removes a sensitive-data exfiltration path through router stderr logs.
mcp.fallbackNotificationHandler = async (notification) => {
  if (notification.method !== 'notifications/claude/channel/permission_request') return;
  const { tool_name = '', description = '', request_id = '' } = notification.params || {};
  const prefixed = `${SESSION_SHORT}-${request_id}`;
  try {
    await routerFetch('/permission-request', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: SESSION_ID, tool_name, description, prefixed_request_id: prefixed }),
    });
  } catch (e) { process.stderr.write(`${TAG} permission-request relay failed: ${e.message}\n`); }
};

module.exports = { mcp };
