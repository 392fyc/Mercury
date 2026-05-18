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

// Tool: reply
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

// Outbound permission_request relay (ADR §5.2 step 6 + §7.6)
mcp.fallbackNotificationHandler = async (notification) => {
  if (notification.method !== 'notifications/claude/channel/permission_request') return;
  const { tool_name = '', description = '', input_preview = '', request_id = '' } = notification.params || {};
  const prefixed = `${SESSION_SHORT}-${request_id}`;
  try {
    await routerFetch('/permission-request', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: SESSION_ID, tool_name, description, input_preview, prefixed_request_id: prefixed }),
    });
  } catch (e) { process.stderr.write(`${TAG} permission-request relay failed: ${e.message}\n`); }
};

module.exports = { mcp };
