# RESEARCH-084: External Agent Access via MCP/HTTP

**TaskId**: TASK-RESEARCH-084
**Issue**: #84
**Date**: 2026-03-28
**Status**: Complete

---

## 1. Executive Summary

Mercury orchestrator 当前通过两种 transport 与外界通信：

1. **stdio JSON-RPC** — Tauri sidecar 模式，GUI 通过 stdin/stdout 与 orchestrator 交互
2. **HTTP JSON-RPC** — localhost:7654，已在 TASK-ARCH-005 中实现

此外已有 **MCP Server** 模式（`--mcp` flag），通过 stdio 暴露所有 RPC 方法为 MCP tools。

本研究的目标是：将 MCP Server 从 stdio-only 扩展为支持 **Streamable HTTP** transport，使外部 Agent（Claude Code、Cursor、其他 MCP client）能通过 HTTP 直接接入 Mercury 工作流，同时保持现有 GUI sidecar 通信不变。

---

## 2. Round 1: Architecture Audit

### 2.1 Transport Layer

| Transport | File | Protocol | Binding | Status |
|-----------|------|----------|---------|--------|
| stdio JSON-RPC | rpc-transport.ts | Line-delimited JSON-RPC 2.0 over stdin/stdout | Tauri sidecar | Production |
| HTTP JSON-RPC | index.ts:327-393 | HTTP POST JSON-RPC 2.0 | 127.0.0.1:7654 | Production (TASK-ARCH-005) |
| MCP stdio | mcp-server.ts | MCP protocol over StdioServerTransport | --mcp flag | Production |

### 2.2 Dispatch Layer

所有三种 transport 最终调用同一入口：`orchestrator.handleRpc(method, params)`

约 45 个 RPC 方法覆盖：Agent Management (6), Session Management (8), Task Management (6), Review Flow (3), Acceptance (2), Critic (2), Issue Management (2), Configuration (2), Knowledge Base (5), Approval Control (5), Context (3), Utility (1).

### 2.3 Event Broadcasting

事件广播通过 `RpcTransport.sendNotification()` 实现。EventBus wildcard 将所有事件转发为 RPC notifications。

当前限制：
- sendNotification 只写入 stdout（stdio transport）
- HTTP JSON-RPC 无事件推送能力
- MCP 模式下 monkey-patch sendNotification 到 sendLoggingMessage()

**关键发现**：事件广播是单通道的，这是扩展的主要挑战。

### 2.4 Persistence Layer

TaskPersistenceDual: SQLite (primary) + KB (sync)。与 transport 完全解耦。

### 2.5 Architecture Diagram

```
                     Tauri GUI (Rust)
                          |
          stdin/stdout (JSON-RPC 2.0, line-delimited)
                          |
               RpcTransport (rpc-transport.ts)
                          |
              Orchestrator.handleRpc(method, params)
              |  Agents  |  Sessions  |  Tasks  |  KB  |
              |  EventBus -> transport.sendNotification  |
                          |
          +---------------+-------------------+
          |                                   |
  HTTP JSON-RPC (127.0.0.1:7654)    MCP stdio (--mcp flag)
  request/response only              McpServer + StdioServerTransport
  (no event push, CORS enabled)     (events -> sendLoggingMessage)
```

---

## 3. Round 2: Technology Research

### 3.1 MCP SDK Transport Options

Installed: @modelcontextprotocol/sdk 1.27.1 (verified locally).
Latest on npm: 1.28.0 (verified 2026-03-28 via https://www.npmjs.com/package/@modelcontextprotocol/sdk).

SDK v1.27.1 includes:

| Transport Class | Import Path | Use Case |
|----------------|-------------|----------|
| StdioServerTransport | server/stdio.js | Local sidecar |
| StreamableHTTPServerTransport | server/streamableHttp.js | HTTP MCP (recommended) |
| SSEServerTransport | server/sse.js | Legacy (deprecated) |

StreamableHTTPServerTransport available since SDK v1.10.0 (2025-04-17).
Verified via npm, SDK docs (https://ts.sdk.modelcontextprotocol.io/documents/server.html), and local .d.ts.

### 3.2 StreamableHTTPServerTransport Features

Verified from local .d.ts and MCP spec (https://modelcontextprotocol.io/legacy/concepts/transports):

- Stateful sessions via MCP-Session-Id header
- SSE streaming for server-to-client events
- Resumability via Last-Event-ID
- DNS rebinding protection via hostHeaderValidation middleware (SDK built-in)
- Compatible with Node.js http.createServer (IncomingMessage/ServerResponse)

Constructor: `new StreamableHTTPServerTransport({ sessionIdGenerator: () => randomUUID() })`
Methods: handleRequest(req, res, parsedBody?), close(), send(), closeSSEStream(), closeStandaloneSSEStream()

### 3.3 Express Helper

SDK provides `createMcpExpressApp()` (server/express.js). Auto-enables DNS rebinding protection for localhost.

**Assessment**: Express dependency too heavy. Use raw `http` + `StreamableHTTPServerTransport` directly.

### 3.4 Existing HTTP JSON-RPC

Already implemented in index.ts:327-393. Uses Node.js built-in http.createServer. Features: CORS, 127.0.0.1 binding, JSON-RPC 2.0 validation.

New MCP HTTP transport should share the same HTTP server (different route) rather than a new port.

### 3.5 Security

| Concern | Mitigation |
|---------|------------|
| DNS rebinding | hostHeaderValidation middleware (MCP SDK built-in) |
| Network exposure | Bind 127.0.0.1 only (already done) |
| Origin validation | MCP spec mandates Origin header check |
| CORS | Tighten from `*` to localhost origins |

Verified via: Node.js security docs (https://nodejs.org/en/learn/getting-started/security-best-practices), MCP spec, Auth0 analysis (https://auth0.com/blog/mcp-streamable-http/).

### 3.6 Technology Selection Matrix

| Criterion | MCP Streamable HTTP | Custom SSE+JSON-RPC | WebSocket |
|-----------|---------------------|----------------------|-----------|
| Standards | MCP spec 2025-03-26 | Custom | Custom |
| SDK support | Built-in (v1.10.0+) | Manual | Requires ws |
| Client compat | Any MCP client | Custom only | Custom only |
| Sessions | Built-in | Manual | Manual |
| Resumability | Built-in | Manual | Manual |
| New deps | 0 | 0 | +ws |
| Effort | ~200 LOC | ~500 LOC | ~800 LOC |
| Ecosystem | Wide (standard) | Mercury-only | Mercury-only |

**Recommendation: MCP Streamable HTTP (Option A)**

### 3.7 TASK-ARCH-005 Status

Commit 9cb519a (2026-03-17): HTTP JSON-RPC on 127.0.0.1:7654 with CORS, validation, configurable port, graceful shutdown. Merged to develop.

---

## 4. Round 3: Architecture Design (Summary)

See DESIGN-084-transport-adapter.md for full design.

Key points:

1. **No new transport abstraction needed** -- handleRpc is already transport-agnostic
2. **Event Fan-out** -- NotificationBroadcaster replaces single-channel sendNotification
3. **Shared HTTP server** -- /rpc (JSON-RPC), /mcp (MCP Streamable HTTP), /events (optional SSE)
4. **Configuration** -- new `transports` block in mercury.config.json

---

## 5. Round 4: Risk Assessment

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| Port conflict | Medium | Medium | Configurable port + error message |
| Concurrent sessions | High | High | Single-instance state; per-transport isolation |
| Event ordering | Low | Low | Per-channel ordering; SSE event IDs |
| Resource leak (SSE) | Medium | Medium | wireShutdown + MCP transport close |
| Breaking changes | High | Low | stdio path unchanged; HTTP/MCP additive |
| SDK version | Low | Low | Semver ^1.27.1 compatible |
| DNS rebinding | Medium | Medium | hostHeaderValidation from SDK |
| Memory growth | Medium | Medium | Max connections + idle timeout |

Compatibility: Tauri GUI (none), HTTP JSON-RPC (none), MCP stdio (preserved), Agent adapters (none), config (additive).

---

## 6. Implementation Roadmap

### Phase 1 -- MCP Streamable HTTP (P1, 3-5 days)
1. Add /mcp route to existing HTTP server for POST/GET/DELETE
2. Create StreamableHTTPServerTransport instance
3. Reuse createMcpServer() tool registrations
4. Wire shutdown hooks
5. Add hostHeaderValidation

### Phase 2 -- Event Fan-out (P1, 2-3 days)
1. Implement NotificationBroadcaster
2. Replace direct sendNotification calls
3. Add optional GET /events SSE endpoint
4. Connection tracking and cleanup

### Phase 3 -- Config + Auth + Polish (P2, 2-3 days)
1. Extend MercuryConfig with transports block
2. Bearer token auth for non-localhost
3. Connection limits and idle timeout
4. Health check endpoint
5. Documentation updates

**Total: 7-11 days**

---

## 7. Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| MCP HTTP transport | StreamableHTTPServerTransport | Standard; SDK built-in; zero deps |
| HTTP framework | Node.js built-in http | Consistent with existing code |
| Port strategy | Shared port 7654 | Simpler config; fewer firewall issues |
| Session mode | Stateful (MCP-Session-Id) | Required for streaming |
| DNS protection | hostHeaderValidation | Official MCP recommendation |
| Abstraction | None (direct routing) | handleRpc already transport-agnostic |

---

## 8. References

- MCP Spec (Transports): https://modelcontextprotocol.io/legacy/concepts/transports
- MCP TypeScript SDK: https://github.com/modelcontextprotocol/typescript-sdk
- MCP SDK npm: https://www.npmjs.com/package/@modelcontextprotocol/sdk
- MCP SDK docs: https://ts.sdk.modelcontextprotocol.io/documents/server.html
- JSON-RPC 2.0 Spec: https://www.jsonrpc.org/specification
- Node.js Security: https://nodejs.org/en/learn/getting-started/security-best-practices
- Why MCP deprecated SSE: https://blog.fka.dev/blog/2025-06-06-why-mcp-deprecated-sse-and-go-with-streamable-http/
- MCP HTTP security: https://auth0.com/blog/mcp-streamable-http/
- SSE MDN: https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events
- TASK-ARCH-005 commit: 9cb519a (2026-03-17)
