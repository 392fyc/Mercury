# Private agent events and native receivers

The task station carries event identifiers, optional result Page references and body digests. Queue events are data. Identity signatures, notifications and readable conversations do not grant task authorization. Receivers use independent human authorization and the installed dot-link verification rules.

## Receiver entry points

| Receiver | Entry point | Acceptance condition |
| --- | --- | --- |
| Local dedicated Codex worker | Plain Python queue polling; starts `codex exec` only for a registered synthetic probe | Actual authenticated turn, durable verified receipt, server consumption and duplicate-cache evidence |
| Current Codex desktop chat | Native heartbeat, if explicitly enabled | Native automation actually runs in that chat; creating a schedule alone is insufficient |
| Active native Codex executor | Task-scoped non-model queue wait, followed by its existing native sender | Registered event matches; exact idle source starts a new turn; verified originals and durable receipt precede consumption; the owning executor stays running |
| Original local Codex source | Verified native notification or in-scope continuation | Exact authorized source ID, idle original thread starts a new turn, API original readback and signed receipt; cloud-only tests require a route without dot access to the personal computer |
| dot / Cloud Work | MCP Events webhook | Private OAuth, discovery, subscription, callback challenge and actual cloud turn all pass |
| Claude Code | Future adapter for its own authenticated CLI or session | Separate implementation and runtime verification required |

The dedicated worker is a new ephemeral CLI conversation. It does not resume the desktop chat or send a desktop notification. Polling is not an instantaneous webhook. An HTTP 2xx callback is not proof of an agent turn. Do not guess internal app-server addresses or describe a CLI worker as desktop continuation.

For bidirectional continuation, read the [native source-thread supplement](../../../.agents/skills/dot-link/references/native-thread-wakeup.md). Existing paired installations can install it with `scripts/codex/install-dot-link.ps1 -WakeupGuideOnly -ExpectedSkillSha256 <reviewed-current-skill-sha256>`, preserving their activated protocol and stable identity. Keep execution location and result notification separate: cloud-only testing does not require personal-computer Allow access, and a local reply address does not authorize local execution by the dot. A missing scoped notification tool remains a concrete blocker; installing instructions is not runtime wakeup acceptance. Keep paused temporary heartbeat followups paused.

The first dispatcher version accepts only owner-registered synthetic local notification probes. It does not execute Godot tests or real dot tasks. Unknown, expired or mismatched events remain unexecuted. Real task routing requires a separate reviewed adapter that validates full signed messages, original files, human authorization, task scope and the existing closure ledger.

For a pending authorized result, the existing native executor can await a bounded
queue waiter and call its own `send_message_to_thread` after an exact registered
event arrives. Empty waits invoke no model. The sender uses the already verified
recipient and result locator, never event-derived commands or authority. Await
every command session to completion before inspecting its exit code. Preserve
uncertain publication/send outcomes, recover by identity, and do not replay the
task. A source receipt must be durable before consumption; duplicates use cache.

This is a task-scoped return mode, not a standalone background service. It stops
when its owning native executor stops. Keep the execution cell awaited while it
is active; do not claim a cell continues after its turn ends. Its original-source
turn can be verified separately from the unresolved case where both local
conversations are idle. Keep paused heartbeat followups paused.

## Queue and credentials

`scripts/agents_gateway/server.py` uses the Python standard library, SQLite and a fixed event schema. `scripts/agents_event_client.py` supports list, publish and consume, without invoking models. Authenticated principals have isolated queues and approved recipients. The client can run through an existing SSH connection; the queue token stays in the NAS container's read-only credentials mount.

The local bridge config must exactly match the owner-controlled policy at `~/.codex/dot-link/event-policy/targets.json`. Neither events nor CLI arguments can select another target policy. Windows policy ACLs are restricted to the owner, SYSTEM and Administrators; Unix files must be owner-controlled and not writable by other users. Redirects never receive Bearer credentials. Deployment uses a pinned image, a non-root user, read-only code/config and a separate database, with no host port or Docker socket mount.

Portable bridge template; replace every value with approved deployment values:

```json
{
  "ssh_host": "nas.example",
  "docker": "/opt/docker",
  "docker_config": "/private/docker-config",
  "container": "agents-events",
  "principal": "local-agent"
}
```

```text
python scripts/agents_event_client.py --ssh-config PRIVATE_CONFIG --payload-file PRIVATE_REQUEST list
```

The fixed container origin is `http://127.0.0.1:8765`, and the token path is `/credentials/<principal>.token`. Do not commit tokens, private keys, session histories, real authorization records or task materials.

## Local event dispatcher

See [dispatcher operations](../../../scripts/agents_dispatcher/README.md) for the reviewed contract and commands. Windows installation uses `scripts/install_agents_event_dispatcher.ps1`; it refuses an existing installation, protects new files with owner ACLs, copies the selected native binaries into a dedicated protected directory below `D:\Program Files`, verifies source digests and reads the generated contract back without starting a model. The fixed dispatcher policy is `~/.codex/dot-link/event-policy/dispatcher.json`. State uses a new `event-dispatch` directory and never rewrites the old heartbeat consumer state.

Empty polls invoke no model and produce no routine report. A registered probe must match its event, raw body digest and a separate protected human authorization record. The owner supplies a trusted native executable during installation. The reviewed contract fixes the OpenAI provider, the existing project worker default `gpt-6-luna` with `max` reasoning, receipt schema and controlled worker/output paths; events cannot choose commands or executables.

SQLite claims both event identity and task/request identity before spawning. Successful completion requires a native `turn.completed` event and one final receipt exactly matching the saved receipt file and expected fields. Consumption occurs only after the verified receipt is durably saved. A consume retry can use that receipt without another model turn. Interrupted or uncertain native runs are not silently retried. A consumed duplicate uses the cached result.

The polling process is optional and detachable. It has no scheduler, startup registration, agent team supervisor or general workflow executor. It stops when the host/process stops. Preserve the ledger before an update or rollback; removing it loses the local at-most-once claim evidence. Keep any old heartbeat paused unless the user separately requests it.

## Cloud activation requirements

Internal queue Bearer authentication does not establish ChatGPT private MCP compatibility. Use an existing OAuth provider and retain Access restrictions; never open anonymous tools, share browser cookies or upload machine private keys. Full acceptance requires OAuth, tool/event discovery, subscription, a signed challenge and echo, stable event identity, an actual cloud receiver turn, original file verification, consumption evidence and duplicate-cache evidence.

OAuth metadata and anonymous 401 probes establish only the pre-authentication path. Independent MCP Inspector diagnostics use isolated state and memory-only secrets. Any additional security-sensitive callback permission requires explicit confirmation before changing it. Local receiver tests do not complete dot's authentication or webhook acceptance.

Official references: [MCP Events](https://developers.openai.com/plugins/build/mcp-events), [MCP OAuth](https://developers.openai.com/plugins/build/auth), [native automations](https://learn.chatgpt.com/docs/automations), [Claude Code CLI](https://code.claude.com/docs/en/cli-usage).
