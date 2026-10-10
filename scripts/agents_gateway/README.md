# Agents event gateway

This small Python service carries fixed task metadata into a durable SQLite queue. A configured publisher can write only to named recipients; each reader is fixed to the principal identified by its bearer token. It does not run a model, start or execute tasks, accept commands, or interpret callback response bodies.

This is a local queue implementation with an MCP Events webhook path behind an internal bearer-token check. It does **not** implement OAuth 2.1, protected-resource metadata, Cloudflare Access, or the ChatGPT/dot machine identity flow. A deployment must independently verify its private OpenAI connection, subscription challenge, recipient binding and actual event-triggered execution. Local tests do not establish those runtime states. Do not expose the service directly to the Internet. External access requires the separately managed Cloudflare Access and machine-identity layer.

Keep the queue container internal-only. Subscriptions need a separately reviewed outbound callback path. The optional fixed-peer relay provides that path without mounting queue state, principal configuration or credentials in the relay; it is disabled unless explicitly configured.

The protocol examples and event method shapes follow the [OpenAI MCP Events guide](https://developers.openai.com/plugins/build/mcp-events), including the `2026-07-28` discovery version, callback challenge, Standard Webhooks signature headers, finite subscriptions, and retry rules. External callback delivery and agent wakeup require runtime acceptance against the configured connection.

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

Generate each bearer token from at least 32 random bytes and place it in the caller's protected, read-only credential mount. Put only its SHA-256 digest in this file. Give the service account read access to the configuration and write access to the SQLite state directory; restrict both with operating-system permissions. SQLite stores queue events, subscriptions, opaque event cursors, webhook signing secrets, and the delivery outbox. Keep its directory private and backed up according to the required queue-retention policy. The service reads the configuration on requests and worker passes but never writes it.

From the repository root, start it with:

```powershell
python scripts/agents_gateway/server.py --config <read-only-principals.json> --database <private-state-path>/agents_gateway.sqlite3
```

Options are `--host` (default `127.0.0.1`), `--port` (default `8765`), and optional `--callback-relay` (an explicitly trusted private HTTP origin). The default binds only to loopback. A reverse proxy can connect locally; keep its access controls outside this service. No deployment files, reverse-proxy rules, or credential files are included here.

## Optional callback relay

`callback_relay.py --peer <fixed-queue-container-ip> --host <private-bind-address>` listens on port 8766 by default. Keep it on the private queue network and a separate outbound bridge, with no published host/public ports. Mount only the two source files; run as a non-root user with a read-only filesystem, no capabilities or privilege escalation, and bounded resources. Source-IP checks rely on the trusted private container network and trusted Docker administration, not Internet authentication.

Start the gateway with `--callback-relay http://<fixed-relay-ip>:8766` only after reviewing and authorizing that deployment. The relay validates HTTPS public destinations, pins resolved addresses, verifies hostname certificates, refuses redirects, and forwards only the original bounded body and five webhook headers. It never receives signing keys or bearer credentials. The relay process has external connectivity; its application rejects non-public callback destinations. Default direct transport behavior is unchanged.

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

Subscriptions have a finite expiry: one hour by default, at most 24 hours, and at least one minute. A requested null or shorter lifetime still receives a finite grant. Subscription state, cursor scope records, and the SQLite outbox survive process restarts. A null or omitted subscribe cursor starts at the current safe event position and does not replay earlier history. The response contains a random opaque cursor bound to the authenticated principal, deterministic subscription ID, event name, and canonical filter. A later subscribe call may pass that cursor to recover matching non-consumed events after its boundary, including events published while the subscription was expired or inactive. A cursor from another principal, filter, callback subscription, malformed token, or oversized token is rejected before callback validation or state changes.

When a valid older cursor explicitly requests replay, absent and cancelled outbox rows are restored, and previously sent events after that cursor are sent again with their original event IDs. Receivers should deduplicate by event ID. Existing retry backoff and unexpired sending leases are preserved; failed rows remain terminal, including `410` and `413`, so renewal cannot restart exhausted or permanent failures indefinitely. Each subscription delivers in event sequence order: an earlier pending retry or active sending lease blocks later events. Subscribe-response cursors stop before the earliest pending or sending event; webhook event cursors identify the current event in that ordered delivery. A 2xx callback acknowledges receipt only. It does not indicate task execution or completion. Local queue consumption cancels pending or sending notifications that have not started their network request, allowing the transport cursor to pass those cancelled rows; it does not turn them into successful webhook deliveries. Task-ledger transitions remain independent of delivery state. The response body is ignored.

Cursor recovery only requeues previously authorized event metadata. It does not publish a new event, admit or claim a task, change task-ledger state, or execute anything. Marking a queue event consumed atomically cancels its pending or sending outbox rows, and the worker rechecks cancellation immediately before starting the callback. Unsubscribe cancels pending rows and preserves a sending lease through that final pre-send check; a callback request that has crossed the send boundary cannot be recalled and may complete, but it is not scheduled again after completion.

There is one application-level pause/resume reconciliation for a **new callback-bound subscription** with a null or omitted cursor: if the same principal and canonical filter had an earlier subscription and the durable queue contains an unconsumed original update with no historical 2xx, terminal failure, exhausted retry budget, or still-live delivery attempt, the gateway may enqueue one new `agent.update` carrying the original task/request/hash/page metadata. The original event and task ledger are unchanged. The signal has an internal reconciliation publisher and a deterministic ID derived from the original event and logical principal/name/filter, so callback URL changes do not create a new signal identity. A signal already accepted with 2xx, consumed, terminally failed, or still held by an active lease is not restarted. When a retryable signal belongs only to an inactive or expired callback, the transaction moves its pending delivery to the new callback with the same event ID and preserved attempt count/backoff; active sending leases are left in place.

This is a gateway policy that emits a new application event from the durable queue state. It is not MCP cursor replay: the response cursor for a new callback remains at the safe pre-signal baseline, and ordinary refreshes stay silent. The [MCP Events documentation](https://developers.openai.com/plugins/build/mcp-events) says missed interruptions for event types without replay cannot be recovered through the protocol; no native dot pause/resume or idle-agent wake behavior is claimed or established here. The signal is only a pointer to outstanding queue metadata: normal receiver checks, complete signed-request validation, human authorization, and the existing claim ledger still govern any work. It cannot trigger a command, promote callback receipt to task completion, or produce a delivery-to-source feedback event.

Transient delivery failures retry with exponential backoff for at most five attempts. Each attempt retains the same body bytes and event ID, and signs those bytes with a fresh timestamp. Expired or revoked subscriptions stop receiving events until a valid refresh; the source event history and cursor records are retained in SQLite for recovery.

## Optional task ledger

The task ledger is a separate, pre-provisioned SQLite database and read-only policy file. Enable it only by supplying both paths:

```powershell
python scripts/agents_gateway/server.py --config <read-only-principals.json> --database <private-state-path>/agents_gateway.sqlite3 --task-ledger <provisioned-task-ledger.sqlite3> --task-ledger-policy <read-only-task-ledger-policy.json>
```

Leave both options unset to keep the task ledger disabled. Supplying only one option, an unavailable ledger database or policy, or an unavailable optional ledger module makes startup fail. The service checks that both files already exist as regular files and never initializes or repairs the ledger database. Provision and back up that database through the ledger's local administration interface; do not place ledger state in the event queue database.

When enabled, the MCP tools `task_ledger_status`, `claim_task`, and `transition_task` are added alongside the unchanged event tools. Their argument fields are fixed:

| Tool | Fields |
|---|---|
| `task_ledger_status` | Optional `request_id` |
| `claim_task` | `request_id`, `body_sha256`, `generation` |
| `transition_task` | `request_id`, `body_sha256`, `generation`, `expected_state`, `state`, `evidence_sha256`; optional `result_page_id` |

`task_ledger_status` reads metadata for the authenticated principal. `claim_task` and `transition_task` record only the fixed request, generation, state, and hash metadata. Callers must use the native dot-link agent to verify message signatures and required human authorization before claiming. The ledger checks the fixed principal identities, policy, and state transitions. A successful claim may return the one-time `start_granted` flag; it is a ledger result, not task execution. The gateway and ledger do not execute tasks, run commands, or interpret task bodies.

Admission is REST-only: the authenticated sender principal calls `POST /api/tasks/admit` with exactly the ledger metadata fields `generation`, `pairing_id`, `key_id`, `client_id`, `recipient_thread_id`, `task_id`, `request_id`, `attempt`, `nonce`, `body_sha256`, `expires_at`, and `protocol_sha256`. Authenticated `GET /api/tasks/status` accepts an optional `request_id`; `POST /api/tasks/claim` and `POST /api/tasks/transition` accept the corresponding fixed tool arguments. Each route passes the authenticated principal ID to the ledger, which enforces sender/receiver identity and policy. The client offers `ledger-status`, `admit`, `claim`, and `transition` operation mappings; `--ssh-config` continues to use the existing fixed host/container/principal policy and the container's read-only token file.

The task ledger does not add a task runner or replace event delivery. The existing `read_queue`, `mark_consumed`, `publish_update`, and `agent.update` event meanings remain unchanged.

## Checks and limits

Run the isolated standard-library tests with:

```powershell
python -B -m unittest discover -s scripts/agents_gateway -p 'test_*.py' -v
```

Tests inject an in-memory callback transport and deterministic public DNS answers. They exercise the local validation and signature code; they do not send a real webhook or prove dot/OAuth/Cloudflare compatibility. The service is designed for a single process on one host. Use the same private SQLite file for restarts; concurrent active replicas and multi-host delivery are not supported.
