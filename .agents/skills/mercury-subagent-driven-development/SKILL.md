---
name: mercury-subagent-driven-development
description: >-
  Mercury 改造版的 subagent-driven-development：按任务派发全新 subagent，配两段式审查
  （先查是否符合规格，再查代码质量）。适用于在当前会话内用隔离的 subagent 执行多任务计划。
  与 superpowers 插件自带的同名 skill **不是同一个** —— 这一份含 Mercury 专属改造
  （#385 的 context 经济学护栏、Windows 优先的路径处理），所以刻意改名并存，
  避免同名条目让模型在两份之间按未文档化的优先级乱选。需要上游原版时用
  superpowers 提供的 `subagent-driven-development`。
user-invocable: true
# Controller-side tool contract: Read/Glob/Grep to locate plan + ledger, Agent to
# dispatch implementer/reviewer subagents, TodoWrite to track tasks, Bash for the
# durable-ledger read/append (cat / echo >>) and git bookkeeping (git log). The
# implementers/reviewers this skill dispatches are separate subagents with their
# own tool grants — this contract is for the coordinating (controller) turn only.
allowed-tools: Read, Glob, Grep, Agent, TodoWrite, Bash
upstream_source: "https://github.com/obra/superpowers"
upstream_sha: "917e5f53b16b115b70a3a355ed5f4993b9f8b73d"
upstream_license: "MIT"
cherry_picked_in: 216
cherry_picked_at: "2026-04-10"
backported_from_sha: "d884ae04edebef577e82ff7c4e143debd0bbec99"
backported_from_tag: "v6.1.1"
backported_in: 509
backported_at: "2026-07-04"
mercury_adaptation: >-
  Retains two-stage spec+quality review split (upstream v6.1.x merged both into
  a single task-reviewer — a style choice, not a bug fix). Back-ports the
  durable ledger recovery, controller-side status handling, and pre-flight
  contradiction scan. Does NOT adopt upstream's bash scripts / .superpowers/
  paths (Mercury is Windows-primary and has its own #385 context-economics
  guardrails).
---

<!-- Cherry-picked from obra/superpowers (MIT, Copyright 2025 Jesse Vincent)
     Initial import: https://github.com/obra/superpowers/blob/917e5f5/skills/subagent-driven-development/SKILL.md
     Initial SHA: 917e5f53b16b115b70a3a355ed5f4993b9f8b73d (2026-04-10, Issue #209)
     Selective back-port: obra/superpowers v6.1.1 (commit d884ae04edebef577e82ff7c4e143debd0bbec99, 2026-07-02)
     Back-port SHA / Issue: d884ae0 / #509 (2026-07-04)
     This is a Mercury-owned adaptation, NOT a verbatim mirror — see frontmatter
     `mercury_adaptation` for what diverges from upstream and why. -->

# Subagent-Driven Development

Execute plan by dispatching fresh subagent per task, with two-stage review after each: spec compliance review first, then code quality review.

**Why subagents:** You delegate tasks to specialized agents with isolated context. By precisely crafting their instructions and context, you ensure they stay focused and succeed at their task. They should never inherit your session's context or history — you construct exactly what they need. This also preserves your own context for coordination work.

**Core principle:** "Fresh subagent per task + two-stage review (spec then quality) = high quality, fast iteration"

**Continuous execution:** Do not pause to check in between tasks. Execute all tasks from the plan without stopping. The only reasons to stop are: a `BLOCKED` status you cannot resolve, ambiguity that genuinely prevents progress, or all tasks complete. "Should I continue?" prompts and progress summaries between tasks waste time — you were asked to execute the plan, so execute it.

> **Mercury governance carve-out.** This "don't pause" rule covers routine in-plan tasks. It does NOT override Mercury's human-gate policy: irreversible, cross-repo, or shared-infrastructure decisions still stop for confirmation (via `AskUserQuestion`), and code still passes `/dual-verify` + PR review before merge. Continuous execution means no busywork check-ins — not bypassing a required gate. If a plan task would trip one of those gates, that is exactly the "ambiguity that genuinely prevents progress" case: surface it.

## When to Use

Use subagent-driven development when you have an implementation plan with mostly independent tasks that you want to execute within the current session. It differs from the "executing-plans" approach by keeping you in the same session while dispatching fresh subagents per task without context pollution.

## The Process

The workflow involves:

1. Reading the plan and extracting all tasks with full context
2. **Pre-flight plan review** — scan the plan once for contradictions before Task 1 (see [Pre-Flight Plan Review](#pre-flight-plan-review))
3. Creating a TodoWrite **and a durable progress ledger** to track progress (see [Durable Progress](#durable-progress))
4. For each task:
   - **Check the ledger first** — a task already marked complete is DONE; do not re-dispatch it (post-compaction recovery)
   - Dispatch an implementer subagent — **hand it a report-file path** (e.g. `.tmp/sdd/task-N-report.md`, alongside the ledger) as its `[REPORT_FILE]`, so it writes its full report to that file instead of into your context (the report-to-file contract in [implementer-prompt.md](implementer-prompt.md) depends on the controller supplying this path)
   - Address any questions before implementation proceeds
   - Implementer implements, tests, commits, and self-reviews
   - **Handle the implementer's reported status** (`DONE` / `DONE_WITH_CONCERNS` / `BLOCKED` / `NEEDS_CONTEXT` — see [Handling Implementer Status](#handling-implementer-status))
   - Dispatch spec compliance reviewer
   - Dispatch code quality reviewer
   - Once approved, mark task complete **in both the todo list and the ledger**
5. After all tasks, dispatch final code reviewer
6. Use your project's branch completion workflow (e.g., Mercury's `/pr-flow` skill).

## Pre-Flight Plan Review

Before dispatching Task 1, scan the plan once for conflicts:

- Tasks that contradict each other or the plan's global constraints.
- Anything the plan explicitly mandates that the review rubric would treat as a defect (a test that asserts nothing, verbatim duplication of a logic block).

Present everything you find as one batched question — each finding beside the plan text that mandates it, asking which governs — **before** execution begins, not one interrupt per discovery mid-plan. If the scan is clean, proceed without comment. The per-task review loop remains the net for conflicts that only emerge from implementation.

## Model Selection

Tailor model capability to task complexity: "Use the least powerful model that can handle each role to conserve cost and increase speed." Mechanical implementation tasks use faster models; integration tasks use standard models; architecture and review tasks use the most capable models.

**Always specify the model explicitly when dispatching a subagent.** An omitted model silently inherits your session's model — often the most capable and most expensive — which defeats this section. (This matters most when the session model is a premium tier: a fan-out of subagents each inheriting it multiplies the cost.)

## Handling Implementer Status

The implementer subagent reports one of four statuses. Handle each appropriately:

- **DONE:** Proceed to the two-stage review (spec compliance, then code quality).
- **DONE_WITH_CONCERNS:** The implementer completed the work but flagged doubts. Read the concerns before proceeding. If they are about correctness or scope, address them before review; if they are observations (e.g., "this file is getting large"), note them and proceed to review.
- **NEEDS_CONTEXT:** The implementer needs information that wasn't provided. Provide the missing context and re-dispatch.
- **BLOCKED:** The implementer cannot complete the task. Assess the blocker:
  1. If it's a context problem, provide more context and re-dispatch (same model).
  2. If the task needs more reasoning, re-dispatch with a more capable model.
  3. If the task is too large, break it into smaller pieces.
  4. If the plan itself is wrong, escalate to the human.

**Never** ignore an escalation or force the same model to retry without changes. If the implementer said it's stuck, something must change before the re-dispatch.

## Key Principles

- Never skip reviews or proceed with unfixed issues
- Always follow spec compliance review before code quality review
- Answer implementer questions completely before allowing them to proceed
- Implement review loops: when issues are found, the same implementer fixes them, then reviewers verify again
- Never dispatch multiple implementation subagents in parallel
- Provide full task context directly rather than having subagents read files
- Record completed tasks in the durable ledger, not only in todos — todos do not survive compaction

## Durable Progress

Conversation memory does not survive compaction — and Mercury routinely runs long, multi-agent Workflows that hit it. A controller that loses its place can re-dispatch an entire completed task sequence, the single most expensive failure this workflow can produce. Track progress in a **ledger file**, not only in todos.

- **Ledger location:** `<repo-root>/.tmp/sdd/progress.md` (Mercury's `.gitignore` already ignores `.tmp/`, so the ledger is git-ignored scratch and never gets committed). If you run this skill outside Mercury, put the ledger under that repo's scratch convention and confirm the path is git-ignored — a committed ledger is a bug.
- **Resolve the ledger path once** with `Bash`, validating the repo root first. A failed `git rev-parse` (not a git repo, or a corrupted checkout) returns empty, and an unvalidated `$ROOT` would point the ledger at `/.tmp/sdd/progress.md` — the wrong location, and a silent loss of progress tracking:
  ```bash
  ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
  [ -n "$ROOT" ] || { echo "ledger: not a git repo — cannot track progress" >&2; exit 1; }   # stop — do NOT proceed unrooted
  L="$ROOT/.tmp/sdd/progress.md"
  ```
  Non-empty is the portable check: `git rev-parse` output is Windows-safe as `D:/…` or `/d/…`, so do NOT additionally require a leading `/` (it would false-reject the `D:/…` form).
- **At skill start,** read any existing ledger: `cat "$L" 2>/dev/null`. Tasks listed there as complete are DONE — do not re-dispatch them; resume at the first task not marked complete.
- **When a task's review comes back clean,** append one line — failing loudly if any step fails, so a lost write never masquerades as saved progress (not losing progress is the whole point of the ledger):
  ```bash
  mkdir -p "$(dirname "$L")"                     || { echo "ledger: mkdir failed" >&2; exit 1; }
  echo "Task N: complete (commits <base7>..<head7>, review clean)" >> "$L" \
                                                  || { echo "ledger: append failed — progress NOT saved" >&2; exit 1; }
  ```
  Get the commit range from `git log --oneline` — `<base7>` is the commit before the task, `<head7>` the task's final commit.
- **The ledger is your recovery map:** the commits it names exist in git even when your context no longer remembers creating them. After compaction, trust the ledger and `git log` over your own recollection.
- `git clean -fdx` will destroy the ledger (it lives in git-ignored scratch); if that happens, reconstruct progress from `git log`.

## Subagent Prompt Templates

See the following files in this skill directory for dispatch templates:

- **[implementer-prompt.md](implementer-prompt.md)** — Template for dispatching implementation subagents
- **[spec-reviewer-prompt.md](spec-reviewer-prompt.md)** — Template for spec compliance review subagents
- **[code-quality-reviewer-prompt.md](code-quality-reviewer-prompt.md)** — Template for code quality review subagents

> **Mercury adaptation — two-stage review split.** Mercury deliberately keeps
> spec compliance and code quality as **two separate reviewer subagents**
> (`spec-reviewer-prompt.md` → `code-quality-reviewer-prompt.md`, dispatched in
> that order). Upstream `obra/superpowers` v6.1.x has since **merged** both into
> a single `task-reviewer` that returns two verdicts in one pass. That merge is
> a style choice, not a bug fix, and Mercury's split is an intentional,
> effective adaptation (a clean spec gate before any quality judgment). The two
> local files are therefore **canonical for Mercury**, not stale references to
> deleted upstream files — do not "fix" them by collapsing into one reviewer.
