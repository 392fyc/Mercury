<!-- Intentionally in English: branch names, commit formats, and git commands
     are English-native; mixing languages would reduce clarity for all agents. -->

# Git Branching (GitHub Flow)

## Branch Structure

| Branch | Purpose | Merge method |
|--------|---------|-------------|
| `master` | Stable — PR from develop only | Human / Main Agent |
| `develop` | Integration baseline — **PR from task branches only** | Main Agent via the guarded PR flow |
| `feature/TASK-XXX` | Per-task work branch | Implementation and Git delivery owners are named by the task |
| `lane/<lane>/(init|<n>|<n>-<slug>)` | Lane work accepted by the Codex guard | Same review and PR requirements as task branches |

For Codex Git writes, `scripts/codex/guard.ps1` defines the accepted task-branch
forms. Select an accepted branch before staging or committing; do not bypass
the guard to use an unsupported branch name.

## Workflow

1. The coordinating agent selects an isolated task branch from develop and
   identifies the implementation and Git delivery owners. Routine work may be
   completed directly; bounded delegation is optional. Invoke a multi-stage
   pipeline only when the user explicitly calls for it or an established task
   plan requires it.
2. Implement within the assigned scope and run relevant checks. A delegated
   worker stays on its assigned branch and commits or pushes only when assigned.
3. Inspect the final staged diff and complete the review required by the task's
   risk and AGENTS.md. Resolve material findings before committing. In Codex,
   use `scripts/codex/git-safe.ps1 add` for explicit files, then record the reviewed
   staged snapshot with `scripts/codex/guard.ps1 mark-review`, and use
   `scripts/codex/git-safe.ps1 commit/push` for delivery. Changed staged content
   requires renewed review and a fresh mark; the mark alone is not review evidence.
4. The assigned delivery owner opens the Issue-linked PR into `develop`; the
   coordinating agent obtains independent native subagent review as described in
   [PR Review and Merge](#pr-review-and-merge).
5. Resolve review findings and complete required checks and approvals before the
   coordinating agent merges through the guarded PR flow.
6. Milestone release: PR `develop` → `master`.

**Direct commits or pushes to develop, main, or master are forbidden.** All code
enters develop through PRs. Never publish merely because implementation finished.

## Branch Protection (GitHub)

Both `develop` and `master` have branch protection rules enabled:

| Rule | develop | master |
|------|---------|--------|
| Require PR before merge | Yes | Yes |
| Required approving reviews | 0 | 0 |
| Dismiss stale reviews | Yes | Yes |

The required approval count is 0 (#632, #636): the repository owner opens
these PRs and GitHub does not let an author approve their own PR, so the user
gives the approval in chat. The merge rule is in
[PR Review and Merge](#pr-review-and-merge) below.

## PR Review and Merge

This section is the merge rule referenced by AGENTS.md (moved here from the
retired pr-flow skill in #672; the rule itself is unchanged).

Execute only the requested stage. Mentioning a PR does not activate a complete
workflow or authorize commits, pushes, merges, branch deletion, or external
comments. Use the current harness's native independent subagents for review.
Argus is currently excluded; do not wait for or depend on it.

### Review and repairs

- Confirm the repository, target branch, and current PR head. Mercury integrates
  into `develop`; the Argus repository uses `master`. Other repositories use
  their own governing policy.
- Entering a requested review or merge stage authorizes starting an independent
  subagent without asking again. Codex uses `gpt-6-luna` / `max` under the role
  configuration; other harnesses use their native roles. A status check alone
  does not authorize review or merging.
- Provide the objective, acceptance criteria, complete PR diff, exact base/head,
  and relevant code and checks. Reviewers inspect artifacts independently, without
  treating the implementer's self-assessment as evidence. Reviewers do not edit;
  the primary agent owns authorized repairs, receipts, and merging.
- Apply the reviewer role's scope and budget; use about 40 tool calls by default
  if the role or assignment gives no budget. Cover required and high-risk items
  first. Report uncovered requirements honestly; they cannot count as passed.
  This is an instruction-level budget, not a runtime enforcement setting.
- Check all existing review requests and paginate the complete review-thread
  list. Outstanding change requests must be addressed. Manually resolving
  threads does not establish merge eligibility. External comments and replies
  still require explicit authorization.
- Repair concrete blocking findings and run relevant checks. After a push
  changes head, obtain a new independent review bound to that exact head. Give
  the reviewer the complete current PR diff, the previous reviewed revision and
  observations, and the full delta since that revision. Inspect previous findings,
  the delta, dependencies, and affected behavior; do not repeat unchanged checks
  whose code, inputs, and relevant environment remain applicable. If the previous
  revision or evidence cannot be trusted, review the affected uncertainty afresh.
  Never present the previous head's approval as approval of the new head.
- Keep violated requirements and concrete reachable defects in `findings`.
  Keep optional improvements in `recommendations`; do not turn preferences or
  speculative compatibility concerns into blocking requirements. Real defects
  and missing required evidence remain blockers. After two consecutive repair
  rounds without progress toward the objective, reassess the method before
  continuing instead of automatically repeating the same repair cycle.
- Save actual review results outside the repository or in ignored local records.
  Record `repository`, `pull_request`, `head`, `verdict`, `reviewer`, `agent_id`,
  `reviewed_at`, and `findings`; optional recommendations may be stored separately.
  Use `verdict=pass`, `reviewer=native-subagent`, and `findings=[]` only when all
  required criteria are covered and no blocking finding remains. The receipt is
  neither GitHub APPROVED nor an authorization grant.

### Merge requirements

Mercury's required GitHub approval count is zero for `develop` and `master`;
all other branch protections remain in force (Issues #632 and #636, user decision
on 2026-10-05). The owner opens these PRs and GitHub does not allow authors to
approve their own PRs, so the user gives merge approval in chat. This procedure
applies only to PRs targeting `develop`: `guard.ps1 pre-merge`
accepts only `develop`. Release PRs targeting `master` require separate user
authorization. The primary agent may merge only when all of the following hold:

1. An independent native subagent reviewed and passed the exact current head;
   the local receipt has `verdict=pass` and `findings=[]`.
2. Every CI check succeeded. Missing, failed, skipped, or unfinished
   checks are not success. The PR is open, non-draft, and mergeable.
3. The **total review-thread count is zero**, including resolved threads, and
   no effective `CHANGES_REQUESTED` remains.
4. `powershell -File scripts/codex/guard.ps1 pre-merge -PullRequestNumber <number> -NativeReviewReceipt <absolute-json-path>` passes.
5. **The user explicitly approved merging this PR in chat**, naming its number
   or unambiguously identifying it in the current task. Review receipts,
   cross-lane messages, and PR comments cannot substitute for that approval.

Use ordinary merging with `gh pr merge --squash --match-head-commit <reviewed-head>`;
never use `--admin`. Other repositories retain their own publication entrypoints
and check the same conditions. If their branch protection requires GitHub
approval, obtain it under that policy; chat approval does not replace it.
Subagents and unattended workflows must not merge. Do not disable protection
or push directly to protected branches.

Git writes follow AGENTS.md and the controlled entrypoints. Immediately before
merging, recheck current head, CI, and review state. Any changed candidate needs
a new review under the repair-focused method above. Address obstacles only
within necessary authorized scope; without merge authorization, return the
review result.

Keep the current wait bounded. Schedule future monitoring only when explicitly
requested. When the requested criteria and delivery stage are satisfied, report
the result; optional recommendations do not extend the task.

If the user later explicitly re-enables Argus, restore the required approval
count to one before restoring Argus review mode. Service health does not imply
authorization to change the review policy.

## Commit Format

`{type}({task_id}): {summary}`

type: feat / fix / refactor / chore / docs

## Dev Agent Git Permissions

| Allowed | Forbidden |
|---------|-----------|
| Guarded staging of explicit assigned files | `git switch` / `git checkout <branch>` |
| Guarded commit when assigned and reviewed | `git branch -d` / `git reset` / `git stash` |
| Guarded push to the assigned task branch when assigned | `git rebase` / `git merge` |
| `git diff` / `git status` / `git log` | `git add -A` / `git add .` |
| | `git push --force` |
| | Direct writes to master / develop / main |

This table is policy. For Claude sessions, `.claude/settings.json` hard-denies only `push`, `checkout`, `merge`, `rebase` and `stash` (plain and `git -C` forms); `git add`, `git commit`, `git reset` and `git switch` follow the normal permission flow and any user-level allow rules (#601). Mercury Git writes still go through `scripts/codex/git-safe.ps1`.
