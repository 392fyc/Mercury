# Agents event gateway

This small Python service carries fixed task metadata into a durable SQLite queue. A configured publisher can write only to named recipients; each reader is fixed to the principal identified by its bearer token. It does not run a model, start or execute tasks, accept commands, or interpret callback response bodies.

This is a local queue implementation with an MCP Events webhook path behind an internal bearer-token check. It does **not** implement OAuth 2.1, protected-resource metadata, Cloudflare Access, or the ChatGPT/dot machine identity flow. The internal bearer check does not establish compatibility with a private OpenAI MCP connection. The initial production mode is the local polling queue; dot authentication, webhook subscription, and an actual dot-triggered run remain pending. Do not expose the service directly to the Internet. External access requires the separately managed Cloudflare Access and machine-identity layer.

The planned NAS container has no public route or callback egress. Webhook subscriptions cannot activate in that deployment; leave event-driven dot delivery pending until the separate identity and network path is reviewed and enabled.

The protocol examples and event method shapes follow the [OpenAI MCP Events guide](https://developers.openai.com/plugins/build/mcp-events), including the `2026-07-28` discovery version, callback challenge, Standard Webhooks signature headers, finite subscriptions, and retry rules. This source has not been connected to a real dot or ChatGPT callback, so external interoperability remains unverified.

## Configuration and start

The read-only JSON configuration contains token digests and recipient grants, never the bearer tokens themselves:

```json
{
  "principals": [
    {
      "id": "local-agent",
      "token_sha256": "<64 lowercase hex characters>",
      "allowed_recipients": []
    },
    {
      "id": "task-publisher",
      "token_sha256": "<another 64 lowercase hex characters>",
      "allowed_recipients": ["local-agent"]
    }
  ]
}
```

Generate each bearer token from at least 32 random bytes and place it in the caller's protected, read-only credential mount. Put only its SHA-256 digest in this file. Give the service account read access to the configuration and write access to the SQLite state directory; restrict both with operating-system permissions. SQLite stores queue events, subscriptions, webhook signing secrets, and the delivery outbox. Keep its directory private and backed up according to the required queue-retention policy. The service reads the configuration on requests and worker passes but never writes it.

From the repository root, start it with:

```powershell
python scripts/agents_gateway/server.py --config <read-only-principals.json> --database <private-state-path>/agents_gateway.sqlite3
```

Options are `--host` (default `127.0.0.1`) and `--port` (default `8765`). The default binds only to loopback. A reverse proxy can connect locally; keep its access controls outside this service. No deployment files, reverse-proxy rules, or credential files are included here.

## Event format and queue API

All routes except `/healthz` require `Authorization: Bearer …`. The service hashes the presented bearer and compares it with the configured `token_sha256` values. It does not log request headers, event bodies, callback URLs, or URL query strings. `/healthz` has no authentication and returns only `{"status":"ok"}`.

`POST /api/events` accepts an object with exactly these fields:

```json
{
  "event_id": "task-update-123",
  "recipient": "local-agent",
  "task_id": "task-123",
  "request_id": "request-456",
  "kind": "completed",
  "body_sha256": "<64 lowercase hex characters>",
  "result_page_id": "optional-page-id"
}
```

`result_page_id` is optional. The `kind` enum is `created`, `started`, `updated`, `completed`, `failed`, `needs_attention`, or `cancelled`. The body itself, instructions, addresses, and private logs are not accepted. `recipient` must exist and appear in the publisher's `allowed_recipients` list. The event insert and matching outbox rows commit in one SQLite transaction. Repeating an identical event from the same publisher returns its stored result; reusing its ID with different metadata returns `409 event_id_conflict`.

`GET /api/events?after=N` reads only the authenticated principal's queue. `after` is the last numeric `seq` seen; there is no recipient-switch query parameter. The response is:

```json
{
  "events": [
    {
      "seq": 1,
      "event_id": "task-update-123",
      "recipient": "local-agent",
      "task_id": "task-123",
      "request_id": "request-456",
      "kind": "completed",
      "body_sha256": "<64 lowercase hex characters>",
      "status": "pending",
      "created_at": "2026-10-04T12:00:00Z"
    }
  ],
  "next_after": 1
}
```

`status` is `pending`, `sent`, or `consumed`. `sent` means a webhook endpoint returned 2xx; it does not mean an agent ran or completed work. The local client consumes one event with `POST /api/consumed` and body `{"event_id":"task-update-123"}`. Consumption is scoped to that principal, is idempotent, and does not publish another event.

## MCP Events and delivery

`POST /mcp` exposes authenticated JSON-RPC methods `server/discover`, `tools/list`, `tools/call`, `events/list`, `events/subscribe`, and `events/unsubscribe`. Discovery advertises `resultType: "complete"`, supported version `2026-07-28`, and `tools` and `events` capabilities. The tools are `read_queue`, `mark_consumed`, and `publish_update`; queue reads and consumption always use the authenticated principal. `initialize` has a minimal MCP 1 compatibility response, which has not been validated against a full MCP 1 client.

The only event is `agent.update`, and its required subscription argument `recipient` must equal the authenticated principal's ID. Webhook subscriptions require an HTTPS URL and a `whsec_` secret containing base64-encoded 24–64 random bytes. A fresh single-use challenge must receive a 2xx response that echoes the challenge before the subscription is stored. The service resolves callback addresses when validating and connecting, rejects non-public IPv4/IPv6 answers, connects to the resolved address while retaining hostname TLS verification, and does not follow redirects. There is no unsafe-callback override.

Subscriptions have a finite expiry: one hour by default, at most 24 hours, and at least one minute. A requested null or shorter lifetime still receives a finite grant. Subscription state and the SQLite outbox survive process restarts. Expired or revoked subscriptions stop receiving events. Transient delivery failures retry with exponential backoff for at most five attempts; `410` and `413` are terminal. Each attempt retains the same body bytes and event ID, and signs those bytes with a fresh timestamp. A 2xx callback acknowledges receipt only. The response body is ignored.

## Checks and limits

Run the isolated standard-library tests with:

```powershell
python -B -m unittest scripts.agents_gateway.test_server -v
```

Tests inject an in-memory callback transport and deterministic public DNS answers. They exercise the local validation and signature code; they do not send a real webhook or prove dot/OAuth/Cloudflare compatibility. The service is designed for a single process on one host. Use the same private SQLite file for restarts; concurrent active replicas and multi-host delivery are not supported.
