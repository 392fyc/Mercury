# PR procedure

How a pull request goes from opened to merged in a project that installs the git-safe pack (Mercury #773, user
decision of 2026-11-10). It applies to every such project; a project's own rules may add conditions but do not
remove these.

## Who does what

- **The main agent** is the session working with the user. Opening a pull request puts it on this procedure: it
  carries the pull request through review, repairs, the Argus review and the merge without asking for a separate
  go-ahead, and reports the result afterwards.
- **Subagents** implement or review. They never merge. A subagent that produced a pull request hands it back to the
  main agent with its branch, head commit and evidence.
- **Argus** is an advisory reviewer: it comments, never approves or requests changes, and reviews only when someone
  comments `@argus-review review`. Each run costs money from a monthly budget. Where Argus is not installed on the
  repository, the pull request says so and step 3 is treated as a review that did not come.

## Steps

1. **Independent review.** An independent native subagent (`mercury-reviewer` on Claude Code) reviews the exact head
   with the complete pull request diff. Repair blocking findings; a repair changes the head, so the changed head gets
   a new review focused on the delta. Record the result outside the repository as a receipt with `repository`,
   `pull_request`, `head`, `verdict`, `reviewer`, `agent_id`, `reviewed_at` and `findings`; `verdict=pass` and
   `findings=[]` only when no blocking finding remains. A receipt is not an approval and grants nothing.
2. **CI.** Every check on the head has succeeded. Missing, skipped, failed or unfinished checks are not success.
3. **Argus on the final head.** When steps 1 and 2 hold, comment `@argus-review review` once. Wait for Argus's review
   of that head with a deadline (15 minutes); if it does not arrive, or Argus reports that its budget is used up,
   say so in the pull request and continue: Argus is advisory.
   - Handle every finding: fix it, or reply in its thread with the evidence why it does not apply.
   - A reply is judged at once: Argus answers in the thread and resolves it if it accepts the reply. Wait for that
     answer (with the same deadline); do not start another review for a reply.
   - A finding fixed by a commit is resolved only when Argus runs again: after any commit that answers a finding,
     comment `@argus-review review` once more on the new head. A commit that changes logic first goes back to
     step 1 for an independent review of the new head.
   - Never resolve a review thread yourself. The only exceptions: you verified that Argus is malfunctioning and
     wrote the evidence in the pull request, or a person allowed it.
4. **Merge gates.** All of these hold on the head to be merged:
   - the receipt from step 1 is for this exact head, with `verdict=pass` and `findings=[]`;
   - Argus reviewed this exact head, or the pull request says why it did not (deadline passed, budget used up, not
     installed). Any change of head after step 3, for whatever reason (a repair, a sync with the integration
     branch), goes back to step 3;
   - every CI check succeeded;
   - no review thread is unresolved, and no `CHANGES_REQUESTED` review is in effect;
   - `guard.ps1 pre-merge -PullRequestNumber <n> -NativeReviewReceipt <absolute path>` passes;
   - the branch protection is satisfied without `--admin`.
5. **Merge.** `gh pr merge <n> --squash --match-head-commit <reviewed head>`. Never `--admin`, never auto-merge, never
   a force push, never a change to branch protection to get a merge through. Recheck head, checks and threads right
   before merging. Then clean up the worktree and branch under the project's worktree rules and report.

## When to stop and ask the user

- A gate can only pass by changing protection, rules or the procedure itself.
- A finding needs a product, design or rule decision that the project reserves for the user.
- The pull request does something the project's rules require explicit authorization for (credentials, external or
  production writes, publication, spending beyond an agreed budget), or a project rule reserves its merge for the
  user (for example an emergency-escalation pull request under the lanes pack, which the user decides).
- Argus threads stay unresolved after two further review runs, Argus's budget is used up while threads are still
  unresolved, or two repair rounds made no progress.

Commenting `@argus-review review` and replying in Argus threads on a pull request the main agent opened, or one its
own subagent handed back, are steps of this procedure and need no separate authorization; other external comments
still do.
