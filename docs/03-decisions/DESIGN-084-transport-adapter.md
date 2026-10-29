# DESIGN-084: Transport Adapter Architecture

**TaskId**: TASK-RESEARCH-084  |  **Issue**: #84  |  **Date**: 2026-03-28
**Status**: Design Complete (pending review)
**Research**: See 04-research/RESEARCH-084-external-protocol.md

All external references web-verified 2026-03-28. SDK version confirmed via npm and local node_modules.

---

## 1. Design Goals

1. External MCP Clients connect Mercury orchestrator via HTTP
2. Preserve Tauri GUI sidecar stdio communication
3. Event broadcast reaches stdio and HTTP clients simultaneously
4. Zero new dependencies (reuse installed MCP SDK)
5. Configuration-driven transport enable/disable

---

## 2. High-Level Architecture

### Target State

```
                     Tauri GUI (Rust)
                          |
          stdin/stdout (JSON-RPC 2.0)
                          |
               RpcTransport (unchanged)
                          |
        +------ Orchestrator.handleRpc ------+
        |                                     |
        |    EventBus -> NotificationBroadcaster
        |       |            |           |
        |    stdio ch    MCP SSE ch   HTTP SSE ch
        +-------------------------------------+
                          |
           http.createServer (127.0.0.1:7654)
             POST /rpc     -> JSON-RPC handler (existing)
             POST /mcp     -> McpServer + StreamableHTTPServerTransport
             GET  /mcp     -> MCP SSE stream
             DELETE /mcp   -> MCP session termination
             GET  /events  -> Native SSE stream (optional)
             GET  /health  -> Health check (P3)
```

### What Changes vs What Stays

Changes: event delivery (single -> multi-channel), MCP mode (stdio-only -> also HTTP), HTTP routes (POST-only -> path-based), config (rpcPort -> transports block).

No changes: handleRpc dispatch, tool registrations, stdio sidecar, agent adapters, persistence, EventBus.

---

## 3. Detailed Design

### 3.1 NotificationBroadcaster

New file: notification-broadcaster.ts (~60 LOC).

Defines NotificationChannel interface (name, send, close) and NotificationBroadcaster class with addChannel, removeChannel, broadcast methods.

Static `wrapTransport(transport)` preserves stdio as primary channel and monkey-patches transport.sendNotification to go through broadcaster. Additional channels (MCP HTTP sessions, SSE streams) are added dynamically.

### 3.2 MCP Streamable HTTP Transport

Extends existing HTTP server with /mcp route. Uses SDK's StreamableHTTPServerTransport (import: server/streamableHttp.js) which handles POST (RPC), GET (SSE), DELETE (session end) per MCP spec.

Per-session architecture: each MCP client gets its own StreamableHTTPServerTransport instance and McpServer instance (factory pattern). Sessions tracked in Map by MCP-Session-Id.

On transport close, session is removed from map and broadcaster channel is cleaned up.

### 3.3 McpServer Factory

Extract registerMcpTools() from mcp-server.ts to allow reuse across stdio and HTTP modes. Factory creates fresh McpServer instance per HTTP session, registers all tools, connects to session transport.

### 3.4 Native SSE Event Stream (P2)

Optional GET /events endpoint for non-MCP clients. Standard text/event-stream with heartbeat. Each SSE connection registers as broadcaster channel, cleaned up on disconnect.

### 3.5 Configuration Model

New TransportsConfig in MercuryConfig:

```
transports:
  stdio: true (default)
  http:
    enabled: true
    host: "127.0.0.1"
    port: 7654
  mcp:
    enabled: true
    mode: "http" | "stdio"
    path: "/mcp"
    sessionManagement: true
  sse:
    enabled: false
    path: "/events"
    maxConnections: 10
    heartbeatIntervalMs: 30000
```

Backward compatible: absent transports field falls back to rpcPort behavior.

---

## 4. Startup Flow

### Default Mode (Tauri sidecar)

1. Parse config -> 2. Create RpcTransport -> 3. Create Orchestrator -> 4. Create NotificationBroadcaster -> 5. Start stdio -> 6. Create HTTP server (routes: /rpc, /mcp, /events) -> 7. Wire shutdown -> 8. Ready notification

### MCP stdio Mode (--mcp flag)

Unchanged. stdio exclusive, no HTTP server.

---

## 5. Security Design

- DNS rebinding: SDK hostHeaderValidation middleware (server/middleware/hostHeaderValidation.js)
- Origin validation: per MCP spec, validate Origin header, reject invalid with 403
- CORS: tighten from * to localhost origins
- Future (P3): bearer token auth for non-localhost; SDK auth/ module for OAuth2

---

## 6. Risk Assessment

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| Port conflict | Medium | Medium | Configurable port |
| Concurrent MCP sessions | High | High | Per-session transport |
| Event ordering | Low | Low | Per-channel ordering |
| SSE resource leak | Medium | Medium | Shutdown hooks |
| Breaking GUI | High | Low | stdio unchanged |
| McpServer multi-session | Medium | Medium | Factory pattern |
| Memory growth | Medium | Medium | Max connections + timeout |

---

## 7. Implementation Phases

### Phase 1: MCP Streamable HTTP (P1, 3-5 days)
- Modify: index.ts (routing), mcp-server.ts (extract registerMcpTools)
- Create: notification-broadcaster.ts
- Config: types.ts (TransportsConfig)

### Phase 2: Event Fan-out + SSE (P1, 2-3 days)
- Modify: orchestrator.ts (broadcaster), index.ts (SSE endpoint)

### Phase 3: Config + Auth + Polish (P2, 2-3 days)
- Modify: types.ts, index.ts, mercury.config.example.json

**Total: 7-11 days**

---

## 8. Testing Strategy

Integration: MCP init, tool invocation, SSE streaming, session lifecycle
Stress: concurrent MCP sessions
Regression: Tauri stdio, HTTP JSON-RPC (/rpc)

---

## 9. Migration Path

1. No breaking changes
2. MCP HTTP on by default, can disable
3. External agents use /mcp immediately
4. rpcPort deprecated in favor of transports.http.port (both work)
