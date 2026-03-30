# Mercury Skill Trigger Strategy

This note defines how `.agents/skills/` should use `openai.yaml` in the Codex environment.

## Policy

- `dispatch-task`: `allow_implicit_invocation: false`
- `acceptance-review`: `allow_implicit_invocation: false`
- `pr-flow`: `allow_implicit_invocation: false`
- `web-research`: `allow_implicit_invocation: true`
- `deep-research`: `allow_implicit_invocation: true`
- `sot-workflow`: `allow_implicit_invocation: true`
- `auto-verify`: `allow_implicit_invocation: true`
- `codex-git-guard`: `allow_implicit_invocation: true`

## Rationale

- Use `false` for workflow skills that mutate Mercury state, dispatch agents, or advance the SoT lifecycle.
- Use `true` for reference or guardrail skills that improve safety, context, or quality without directly changing orchestration state.
- Keep execution skills explicit so Codex does not accidentally create tasks, start acceptance, or advance a workflow on a loose keyword match.
- Keep safety/reference skills implicit so Codex can proactively pull them in when the user omits the exact trigger words.

## Decision Rule

Choose `allow_implicit_invocation: false` when the skill:
- creates or dispatches a task
- records acceptance or otherwise changes lifecycle state
- depends on operator intent being explicit

Choose `allow_implicit_invocation: true` when the skill:
- provides background workflow knowledge
- verifies external dependency claims
- runs a local quality gate before commit
- blocks protected-branch git mutations through explicit preflight checks

## Consistency

The policy table above mirrors each skill's `openai.yaml`. To prevent drift:
- When adding/modifying a skill's `openai.yaml`, update this table in the same commit.
- Future CI validation: parse `openai.yaml` files and assert they match this table (tracked in TASK-WF-001).

## Codex Notes

- `description` remains the primary trigger surface. `openai.yaml` only controls whether Codex may invoke the skill implicitly.
- Keep descriptions slightly proactive, with English-first wording plus Chinese trigger phrases.
- Prefer PowerShell examples and `Invoke-RestMethod` in skill bodies; do not rely on Claude-specific hook behavior.
