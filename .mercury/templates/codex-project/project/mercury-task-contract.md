# Portable task and evidence contract

This contract supports bounded delegation and independent review when needed.
The target repository chooses its execution and publication policy. A single
executor may handle routine work; this template does not require a pipeline or
two separate model reviews for every task. Invoke a complex workflow or
multi-stage pipeline only when the user explicitly calls for it or an established
task plan requires it.

## Task input

State the objective, acceptance criteria, relevant contracts, allowed write
paths, and required verification. For delegated repository work, identify the
target repository, branch or worktree, and starting revision. Record forbidden
paths, protected state, and dependencies when relevant. Use a task identifier
when the project or coordinating agent requires one.

Resolve routine details from repository context. Report an ambiguity or changed
branch, HEAD, or governing contract when it can materially change the result;
pause dependent writes while the mismatch is resolved. Preserve concurrent work.
Commit and publication actions require explicit inclusion in the assignment and
completion of the repository's review and guarded Git requirements.

## Scope, verification, and completion

- Derive acceptance criteria, allowed write paths, and required checks from the
  user's objective and governing contracts. Do not invent acceptance criteria
  to justify speculative hardening. A short task needs no separate plan file.
- Organize work into coherent, independently verifiable outcomes. Do not split
  each line, field, or assertion into a separate delegation or review cycle.
- Add tests only for a stated requirement, a reproduced defect, or a concrete
  failure reachable through the changed behavior. For reversible, low-impact
  edits, avoid tests that merely mirror the implementation. Existing required
  checks and relevant security or data-integrity tests remain mandatory.
- Once required checks pass, repeat or broaden verification only for changed
  inputs, failures, or an unresolved concern supported by evidence. Reuse an
  observed result only while its code, inputs, and relevant environment remain
  applicable; recheck mutable publication state at the required delivery gate.
- Record out-of-scope observations without investigating or implementing them.
  If one prevents the authorized objective, explain the dependency and the
  smallest necessary scope decision; continue work that does not depend on it.
- Judge progress by the requested outcome, not the number of tests, receipts,
  hashes, or review rounds. After two consecutive repair rounds without progress
  toward that outcome, reassess the approach before continuing. Use a simpler
  in-scope approach when justified; request a decision only when authorization,
  a governing contract, or a material missing choice prevents sound progress.
- Run provenance or hash checks when required for an import, installation,
  upgrade, synchronization, publication, or evidence-backed drift investigation.
  Use the governing verifier and its stated threat model. Do not rebuild an
  equivalent verifier, broaden compatibility guarantees, or re-audit unchanged
  layers without a task-relevant reason. Preserve required exact-byte checks,
  review-to-candidate binding, and shared-file write preconditions.
- When acceptance criteria, required checks, and required independent review
  are satisfied, perform the authorized delivery steps and report the result.
  Optional improvements do not extend the task. If a budget, missing access, or
  unresolved requirement prevents completion, disclose the gap; never count
  unexamined or failed requirements as passed.

## Evidence

Each criterion needs a reproducible observation: a command and observed result,
file-and-line citation, or runtime observation. Identify the candidate revision
or diff examined; include exit status for commands and collection time when the
underlying state is mutable. Distinguish direct evidence from inference and keep
failures, skipped checks, and limitations visible.

Never copy credentials, secret-matching text, or private content into evidence.
Use logical repository identities and repository-relative paths in portable
records; exclude machine paths, tokens, ports, and transient process state.

## Implementation receipt

For a routine bounded task, return changed files, criterion-specific evidence,
commands and results, and limitations. Include branch and commit identifiers
when relevant; an uncommitted candidate is valid.

Use the following full receipt for cross-repository work, changes requiring
independent review, or a task that explicitly requests structured exchange:

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

## Review and completion

Choose review depth from the task's risk and repository policy. Substantial
behavior changes, cross-repository writes, security or permission changes, and
agent instructions or rules require independent review. The task may choose
change review, blind acceptance, or both according to the uncertainty involved.
A workflow explicitly invoked by the user or required by an established task
plan may specify additional reviewers.

Independent reviewers inspect the exact candidate and gather fresh evidence;
the implementer's self-assessment cannot substitute for their evaluation. Blind
acceptance receives the objective, criteria, candidate, changed files, and
verification evidence without implementation reasoning.

Completion requires satisfied criteria, successful required verification,
protected state preserved, and material review findings resolved. Disclose any
remaining blocked or skipped check without converting it into a passing claim.
