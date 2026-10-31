# Task and evidence contract

Installed by Mercury (pack `core`) at `.mercury/packs/core/docs/mercury-task-contract.md`; do not edit the installed copy.
The managed section of AGENTS.md points here.

This contract supports bounded delegation and independent review when needed.
The project chooses its execution and publication policy. A single
executor may handle routine work; this contract does not require a pipeline or
two separate model reviews for every task. Invoke a complex workflow or
multi-stage pipeline only when the user explicitly calls for it or an established
task plan requires it.

## Task input

State the objective, acceptance criteria, relevant contracts, allowed write
paths, and required verification. For delegated repository work, identify the
target repository, branch or worktree, and starting revision. Record forbidden
paths, protected state, and dependencies when relevant. Use a task identifier
when the project or coordinating agent requires one.

When the project keeps a knowledge base (KB), name the KB documents that govern
the task (current rules, decisions, progress) and read them before editing.
When the change alters a rule, a decision or progress, update the current
document and mark what it replaces in the same change; otherwise state that it
has no KB impact and why. The `kb` pack's conventions and checks apply where the
project selects it.

Resolve routine details from repository context. Report an ambiguity or changed
branch, HEAD, or governing contract when it can materially change the result;
pause dependent writes while the mismatch is resolved. Preserve concurrent work.
Commit and publication actions require explicit inclusion in the assignment and
completion of the repository's review and guarded Git requirements.

## Explanation and reasoning

These rules apply to the main agent and every subagent, in Codex and Claude Code.

- Explain the Feynman way. Write explanations, reports, reviews, and handoffs so
  that someone outside the task can follow them: plain words, each term the
  reader may not know defined, and each point backed by a concrete example or
  the actual evidence. A point you cannot explain simply is a gap in
  understanding: close it from the source or mark it unresolved instead of
  covering it with jargon.
- Reason from first principles in research, design, and investigation. Start
  from the goal, the hard constraints, and facts verified in primary sources or
  by direct observation. Treat conventions, analogies, existing implementations,
  earlier conclusions, and other agents' claims as hypotheses to check. State the
  assumptions a conclusion depends on and label conclusions that rest on
  unverified assumptions. Check those assumptions in proportion to risk:
  evidence that stays applicable under the evidence rules below counts as
  verified fact, and user decisions and authorization are constraints, not
  hypotheses.
- When delegating to an agent that does not load these instructions, including
  one that works in another repository or directory with its own instructions,
  restate both rules in the delegation prompt.

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

## Waiting on long-running work

The host does not bound a wait for you (Mercury #706). Claude Code applies a time
limit to background commands only in unattended sessions (`-p`, Agent SDK, CI,
cloud); terminal, desktop and IDE sessions have none, and a foreground command
that reaches its timeout moves to the background instead of stopping. `maxTurns`
does not help either: a subagent that waits uses no turns. A wait whose condition
can never come true therefore runs until a person notices.

- Pick the deadline from the expected duration (about twice the expected time)
  and state it. Bounded forms:
  - Bash: `timeout 900 bash -c 'until [ -f out/done ]; do sleep 5; done'`, or a
    loop that compares `$SECONDS` with an end time.
  - PowerShell: `$end = (Get-Date).AddMinutes(15); while (-not (Test-Path out/done) -and (Get-Date) -lt $end) { Start-Sleep 5 }`.
  - Claude Code `Monitor` with `timeout_ms` (at most 30 minutes; start it again
    after it expires only for a stated reason).
- A command started in the background already reports its exit. Wait for that
  notice; do not add a loop that polls the command's output file for a pattern.
  When a separate wait is unavoidable, its end condition must include the
  watched command's exit (for example the host's `exited with code` line) and
  every failure state, never only a count of success lines.
- At the deadline, stop waiting and report that the work did not finish, with
  the last output. Start a longer wait only for a stated reason.
- A delegation that may wait states the deadline. Before reporting completion,
  list the background tasks you and your subagents started; stop each one past
  its deadline or no longer needed, and report what was stopped.

**Guard.** Every Claude role Mercury generates (the `mercury-*` roles and the
project-area roles) runs `.mercury/packs/core/hooks/wait_guard.sh` as a
`PreToolUse` hook on Bash and PowerShell. It blocks a command that contains a
wait without a deadline:

- a loop that sleeps in its condition or body (`while|until <cond>; do ... sleep ... done`,
  PowerShell `while (<cond>) { ... }` or `do { ... } while|until (<cond>)` with
  `Start-Sleep`), unless its condition reads the clock (`$SECONDS`, `date +%s`,
  `Get-Date`, `Stopwatch` `.Elapsed`, `[datetime]::Now`), counts a variable up to
  a number, or reads input (`while read`);
- a command that follows or watches until stopped: `tail -f`, `inotifywait -m`,
  `Get-Content -Wait`, `journalctl -f`, `docker`/`kubectl logs -f`,
  `gh ... --watch`, `gh run watch`, `watch`, `sleep infinity`, and `Wait-Job` or
  `Wait-Process` without `-Timeout`;

unless the top-level command that holds it is `timeout <duration> ...` (a
duration above 0; `timeout 0` means no limit) in the Bash tool (in PowerShell, `timeout` is Windows' `timeout.exe`, which bounds
nothing). A `timeout` on another command of the same line does not count. The
guard reads text, not behavior: a deadline that is present but wrong passes, a
wait written in another form (for example inside `python -c`) is not seen, text
in quotes counts too (a search pattern or a message that names `tail -f` or
holds a whole loop is refused), and an unusual bounded loop may be refused; the
refusal says how to rephrase. A sleep placed after an inner loop of a nested loop
is not seen. Endless `for ((;;))` and `for (;;)` loops are
refused as well. It does
not run in the main session, in other agents or in Codex, where these rules
still apply. When no Python 3.8+ is available, or the check cannot run, it allows
the call (and says so on stderr when Python is missing), so that a broken check
does not stop all shell work.

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
commands and results, the KB documents read and updated (or "no KB impact"),
and limitations. Include branch and commit identifiers
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
