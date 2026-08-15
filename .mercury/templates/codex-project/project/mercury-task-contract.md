# Portable task, evidence, and receipt contract

This contract defines the minimum information exchanged between an orchestrator,
an implementation worker, and independent reviewers. A project may add fields or
stricter gates, but it must not silently weaken these requirements.

## Task bundle

Before implementation begins, record:

- `task_id` and a bounded `objective`;
- independently testable `acceptance_criteria`;
- `target_repository`, `target_branch`, and exact starting `target_head`;
- `allowed_write_paths` and `forbidden_paths`;
- governing `contracts`, each with a path or stable identifier and a concise
  `contract_summary` captured before the first write;
- required `verification_commands` and protected state that must remain unchanged;
- dependencies, known constraints, and escalation conditions.

The implementer must stop when the target HEAD, branch, or governing contract no
longer matches the bundle and the difference can affect the result.

## Evidence contract

Evidence is a reproducible observation, not an assertion. Each item records:

- the acceptance criterion it supports;
- the command, test, file-and-line citation, or runtime request used;
- the observed result and exit status when applicable;
- the target HEAD or candidate revision on which it was collected;
- collection time when the underlying state is mutable;
- limitations, skipped checks, and whether the result is direct evidence or an
  explicitly labelled inference.

Sensitive matches, credentials, and private content must never be copied into a
receipt. Report their category, affected scope, and disposition instead.

## Implementation receipt

Return a machine-readable object with at least these fields:

```json
{
  "task_id": "stable identifier",
  "status": "completed|blocked|failed",
  "target_repository": "logical repository identity",
  "target_branch": "assigned task branch",
  "target_head_before": "full commit identifier",
  "candidate_head": "full commit identifier or null for an uncommitted candidate",
  "contract_summary": [
    {"contract": "path or identifier", "summary": "governing points"}
  ],
  "changed_files": ["repository-relative path"],
  "verification": [
    {"command": "reproducible command", "result": "pass|fail|skipped", "evidence": "concise observation"}
  ],
  "criteria_evidence": [
    {"criterion": "criterion text", "result": "pass|fail|partial", "evidence": ["citation or command result"]}
  ],
  "protected_state": [
    {"subject": "protected path or repository", "result": "unchanged|changed|unverified", "evidence": "concise observation"}
  ],
  "residual_risks": [],
  "escalation_reason": null
}
```

Use repository-relative paths and logical repository identities. Do not store
local absolute paths, secrets, tokens, service ports, or transient process state.

## Review and acceptance

Implementation review checks the candidate diff for correctness, scope,
maintainability, security, and regression risk. Acceptance is a later blind pass
against the criteria and receives no implementation reasoning. Both must state
the exact candidate reviewed and provide fresh evidence for their verdict.

Completion requires all criteria to pass, required verification to succeed, the
receipt to be complete, protected state to remain unchanged, and material review
findings to be resolved. A blocked or skipped check remains visible in the final
receipt and cannot be converted into a passing claim.
