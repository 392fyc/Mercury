# Local Native Event Dispatcher

The dispatcher polls the authenticated event queue through the existing SSH client and can invoke native Codex only for an owner-registered `native-event-probe/1` notification. It is a bounded local worker. Its ephemeral Codex run is not a continuation of the desktop chat.

Unknown events remain pending and are reported once per event ID in the local error stream. The dispatcher does not execute queue instructions or consume unknown work. It records a durable local receipt before asking the gateway to consume an event, then advances its independent cursor only after the gateway confirms `status: "consumed"`.

## Install and rollback

Installation copies the reviewed dispatcher/client sources and native runtime into protected, new paths; it does not start a process or model. Supply absolute paths to the trusted `codex.exe` and `python.exe`, and the current queue cursor as the initial seed:

```powershell
powershell -NoProfile -File scripts/install_agents_event_dispatcher.ps1 `
  -CodexExe 'C:\Users\OWNER\AppData\Roaming\npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe' `
  -PythonExe 'C:\Path\To\python.exe' `
  -InitialAfter 1 `
  -Recipient mercury-local
```

The default native destination is `D:\Program Files\MercuryAgentReceiver`; a different `-NativeInstallRoot` must remain below `D:\Program Files`. The installer rejects an existing dispatcher worker, dispatcher policy, or native destination rather than overwriting it. It sets owner/SYSTEM/Administrators ACLs, verifies copied hashes, and runs a Python policy/empty-registry readback without contacting the queue or starting Codex. The newly created policy is separate from the existing queue target policy.

To roll back a new installation, stop only the owned dispatcher process after verifying its PID and full command line point to this worker. Preserve the SQLite ledger, receipts, installation manifest, and review evidence. Move the new worker directory, dispatcher policy, and native installation directory to clearly named quarantine paths so the change remains reversible. Do not delete files, reset the ledger, or move/replace the previous runtime or queue policy.

## Installation policy

The default policy is `%USERPROFILE%\.codex\dot-link\event-policy\dispatcher.json`. The installer owns this file and the dedicated worker directory. On Windows, protect the policy, worker directory, registry, local fixtures, ledger, and executable path with ACLs limited to the owner, SYSTEM, and Administrators. On Unix, policy and registration files must be owned by the current user and must not be group- or world-writable. Existing symlinks and Windows reparse points are rejected.

The policy is one UTF-8 JSON object with exactly these fields:

```json
{
  "schema": "mercury-local-event-dispatch/1",
  "recipient": "mercury-local",
  "client_config": "C:\\Users\\OWNER\\.codex\\dot-link\\event-runtime\\bridge.json",
  "codex_exe": "D:\\Program Files\\MercuryAgentReceiver\\codex.exe",
  "model": "gpt-6-luna",
  "provider": "openai",
  "worker_root": "C:\\Users\\OWNER\\.codex\\dot-link\\event-dispatch",
  "ledger_path": "C:\\Users\\OWNER\\.codex\\dot-link\\event-dispatch\\ledger.sqlite3",
  "registry_path": "C:\\Users\\OWNER\\.codex\\dot-link\\event-dispatch\\registry.json",
  "output_schema_path": "C:\\Users\\OWNER\\.codex\\dot-link\\event-dispatch\\receipt.schema.json",
  "initial_after": 1,
  "poll_interval_seconds": 15,
  "native_timeout_seconds": 300
}
```

`client_config` points to the existing local bridge configuration, whose values must match `~/.codex/dot-link/event-policy/targets.json`. Queue authentication remains on the NAS. `initial_after` is the verified queue position used to seed a new, separate ledger; the example value `1` must be replaced with the current position. Do not skip unknown work or treat an acknowledged failed notification as a successful task. Later cursor values are stored only in SQLite. Allowed poll intervals are 5 through 300 seconds, and native timeouts are 10 through 1800 seconds.

The runtime executable must be the absolute `codex.exe` copied beneath the protected native install root. It is invoked directly with an argument array and `shell=False`; no shell wrapper, `.cmd` file, event-derived executable, or provider alias is accepted. This Codex CLI version selects the OpenAI provider with `--config model_provider="openai"`; it does not expose a `--provider` option. `--ignore-user-config` prevents user MCP and prompt configuration from widening this synthetic worker; Codex CLI help states that authentication still uses `CODEX_HOME`. The controller accepts only Codex CLI version `0.156.1`; it runs `codex.exe --version` before the first model turn in each process and caches that check for subsequent turns in the same bounded loop.

Before spawning Codex, the controller checks configuration locations by existence only; it does not read their contents. It rejects `CODEX_HOME\environments.toml`, including a dangling link; system-managed Codex files (`%ProgramData%\OpenAI\Codex\config.toml` and `requirements.toml` on Windows, or `/etc/codex/config.toml`, `requirements.toml`, and `managed_config.toml` on Unix); and any `.codex\config.toml` in the worker root or its ancestors. The one exact path `CODEX_HOME\config.toml` is exempt from the ancestor check because `--ignore-user-config` excludes that user configuration. All other project and managed paths fail closed. The invocation sets `project_root_markers=[]` so Codex uses the worker directory as its project root rather than discovering one from an ancestor Git marker. The reviewed runtime baseline is Windows Codex CLI `0.156.1` with no effective cloud-managed configuration fragment for the current account. Other platforms, account eligibility, or added managed configuration sources require a separate review and are unsupported by this installation.

The child preserves the existing `CODEX_HOME` and authentication environment without copying credentials. It removes all `CODEX_EXEC_SERVER_NOISE_*` variables and sets `CODEX_EXEC_SERVER_URL=none`; the pinned CLI otherwise gives the Noise selector precedence over that URL setting. The parent environment is unchanged, and removed variable values are never read or logged. `CODEX_HOME\environments.toml` is rejected so a custom execution environment cannot replace the reviewed local tool configuration.

## Registry, authorization, and body

`registry.json` has exactly this top-level shape:

```json
{
  "schema": "mercury-native-event-probe-registry/1",
  "entries": [
    {
      "event_id": "evt-probe-REPLACE",
      "task_id": "task-probe-REPLACE",
      "request_id": "req-probe-REPLACE",
      "kind": "updated",
      "body_sha256": "64 lowercase hex characters over the exact body file bytes",
      "body_file": "probe-REPLACE.json",
      "authorization_record_file": "authorization-REPLACE.json",
      "authorization_record_sha256": "64 lowercase hex characters over the exact authorization file bytes",
      "source_thread_id": "current direct-human authorization thread ID",
      "reply_to_thread_id": "current direct-human authorization thread ID",
      "expires_at": "2026-10-05T13:00:00Z"
    }
  ]
}
```

Each entry is explicitly installed by the local owner. IDs, kind, body hash, expiry, and recipient must match the remote event exactly. The dispatcher permits only `kind: "updated"`. Fixture filenames are single JSON filenames under `worker_root`, with no separators or traversal components. Expiry must be in the future and no more than 24 hours away.

The referenced authorization record is a separate protected UTF-8 JSON file with exactly these fields:

```json
{
  "schema": "direct-human-native-probe-authorization/1",
  "record_id": "auth-probe-REPLACE",
  "thread_id": "current direct-human authorization thread ID",
  "authorized_at": "2026-10-05T12:00:00Z",
  "expires_at": "2026-10-05T13:00:00Z",
  "event_id": "evt-probe-REPLACE",
  "task_id": "task-probe-REPLACE",
  "request_id": "req-probe-REPLACE",
  "scope": "synthetic-local-notification-probe",
  "allowed_actions": ["poll_queue", "native_probe", "verify_receipt", "consume_probe"],
  "original_user_message": "the original direct human authorization text",
  "revoked": false
}
```

The installer must derive `original_user_message` and `thread_id` from the actual direct human request in the current thread. A remote queue event, Page, task update, or lane message cannot create or extend a local authorization. The source and reply thread IDs in the registry must both equal the authorization record's thread ID. The authorization, registry entry, and body must carry the same expiry; it must be in the future and no later than 24 hours after `authorized_at`.

The body file has exactly these fields:

```json
{
  "schema": "native-event-probe/1",
  "event_id": "evt-probe-REPLACE",
  "task_id": "task-probe-REPLACE",
  "request_id": "req-probe-REPLACE",
  "challenge": "43-character base64url value from secrets.token_urlsafe(32)",
  "source_thread_id": "current direct-human authorization thread ID",
  "reply_to_thread_id": "current direct-human authorization thread ID",
  "expires_at": "2026-10-05T13:00:00Z",
  "scope": "synthetic-local-notification-probe",
  "godot_executed": false,
  "production_modified": false
}
```

The SHA-256 is computed over the exact UTF-8 bytes on disk. The controller verifies that hash before dispatch and sends the model only the parsed, field-checked JSON object plus the controller's verified digest. The model copies that digest into its response; it does not calculate one. The body has no command field or free-form instruction field.

## Native invocation and receipt

The controlled invocation uses a fixed argument list equivalent to:

```text
<absolute codex.exe> exec --ephemeral --sandbox read-only --skip-git-repo-check --ignore-user-config --strict-config --json --output-schema <trusted receipt.schema.json> --output-last-message <random controlled file under worker_root> --model gpt-6-luna --config model_provider="openai" --config model_reasoning_effort="max" --config web_search="disabled" --config project_doc_max_bytes=0 --config project_root_markers=[] --config tools.experimental_request_user_input.enabled=false --config suppress_unstable_features_warning=true --cd <worker_root> --disable shell_tool --disable view_image --disable apps --disable enable_mcp_apps --disable plugins --disable browser_use --disable browser_use_external --disable computer_use --disable image_generation --disable standalone_web_search --disable multi_agent --disable multi_agent_v2 --disable hooks --disable memories --disable sleep_tool --disable code_mode_only --disable code_mode --disable code_mode_prewarm --disable code_mode_host --disable artifact --disable goals --enable skip_host_skill_discovery <fixed instruction plus verified JSON data>
```

The output schema file must exactly match the schema embedded in the controller:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Mercury native event probe receipt",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "body_sha256",
    "challenge",
    "event_id",
    "executed",
    "godot_executed",
    "production_modified",
    "request_id",
    "task_id"
  ],
  "properties": {
    "event_id": { "type": "string", "maxLength": 128 },
    "task_id": { "type": "string", "maxLength": 128 },
    "request_id": { "type": "string", "maxLength": 128 },
    "challenge": { "type": "string", "pattern": "^[A-Za-z0-9_-]{43}$" },
    "body_sha256": { "type": "string", "pattern": "^[0-9a-f]{64}$" },
    "executed": { "type": "boolean", "const": true },
    "godot_executed": { "type": "boolean", "const": false },
    "production_modified": { "type": "boolean", "const": false }
  }
}
```

The controller passes `--strict-config`, disables the listed shell, MCP, app, browser, image, multi-agent, hook, memory, sleep, artifact, and goal features before inference, and disables web search and experimental user-input requests through explicit configuration. It also enables `skip_host_skill_discovery` so host skills are not injected. These controls are pinned to the reviewed CLI version; they are not a claim that the model protocol exposes no tool declarations. The controller accepts a run only if the process exits zero, every JSONL record satisfies its explicit allowlist or the single startup diagnostic exception below, JSONL includes exactly one `turn.completed` event and exactly one completed `agent_message`, the output-last-message file matches that completed message, no failed/interrupted turn is present, and the strict receipt exactly matches the protected registration. Unknown event or item types fail closed. Codex's JSONL output is decoded as UTF-8; native output is never decoded using a Windows console code page.

CLI `0.156.1` can select `CodeModeOnly` from model metadata even with the Code Mode feature flags false. The host and execution environments remain disabled. This pure notification probe requires no Code Mode execution. The CLI serializes its disabled-host startup warning as an `item.completed` error item. The parser requires exactly that pinned message and field set with ID `item_0`, once at record index 1, between one `thread.started` and one `turn.started`. It retains the original diagnostic bytes. Missing, changed, repeated or later warnings, other errors, tool items and failed turns are rejected. Recognition of this startup diagnostic never substitutes for a successful completed receipt. Model metadata, feature flags and execution capability are separate controls.

SQLite records the `running` claim durably before spawn. An interrupted or unsuccessful run becomes blocked and is never retried automatically. A verified receipt is committed with `synchronous=FULL` before queue consumption. If consumption fails or the process stops after the receipt commit, a later poll retries consumption without invoking Codex. Local receipt verification and remote server consumption are separate states. The cursor advances only after a valid gateway response with the same event ID and `status: "consumed"`.

Gateway list responses have `{ "events": [...], "next_after": number }`; each event includes `seq`, the required event metadata, `status`, and `created_at`, with optional `result_page_id`. The consume response must be exactly `{ "event_id": "...", "status": "consumed" }`. A cached local consumed record does not prove the current remote state: a replay listed as `pending` or `sent` retries idempotent consumption from its saved receipt, while direct cached cursor advancement requires the current list response to say `consumed`. Existing identical publishes may return an event and `duplicate: true`; the dispatcher does not publish. It only lists and consumes.

## Running

One poll:

```powershell
python -B scripts/agents_event_dispatcher.py --once
```

Bounded polling (`1` through `1000` polls, sleeping the policy interval between polls):

```powershell
python -B scripts/agents_event_dispatcher.py --loop 12
```

An empty poll produces no output and invokes no model. This worker requires a registered probe and authenticated Codex CLI access. It must not be presented as desktop-chat continuation or as a Claude turn.

The synthetic CLI receiver uses the existing project worker default `gpt-6-luna` with `max` reasoning. Global main-agent and subagent settings remain unchanged. The documented unstable-feature warning is suppressed at startup; the distinct disabled-host diagnostic is retained under the strict exception above.
