---
name: pr-flow
description: Review a requested PR or execute an explicitly requested PR stage using native independent agents. Schedule repeated checks only when ongoing monitoring is explicitly requested.
---

# PR checks and independent review

Execute only the requested stage. Mentioning a PR does not activate a complete
workflow or authorize commits, pushes, merges, branch deletion, or external
comments. Use the current harness's native independent subagents for review.
Argus is currently excluded; do not wait for or depend on it.

## Review and repairs

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

## Merge requirements

Mercury's required GitHub approval count is zero for `develop` and `master`;
all other branch protections remain in force (Issues #632 and #636, user decision
on 2026-10-05). The owner opens these PRs and GitHub does not allow authors to
approve their own PRs, so the user gives merge approval in chat. This procedure
applies only to PRs targeting `develop`: `guard.ps1 pre-merge` accepts only
`develop`. Release PRs targeting `master` require separate user authorization.
The primary agent may merge only when all of the following hold:

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
