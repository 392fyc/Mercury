# DEC-2026-11-10: PR procedure with Argus, merged by the main agent

Status: current. Decided by the user on 2026-11-10 (#773, Argus #56).

## Decision

1. Argus reviews only when someone comments `@argus-review review`; opening a pull request or pushing to it starts
   nothing, because every run costs money from a monthly budget.
2. The PR procedure applies to every project that installs the git-safe pack, with no difference between
   repositories. Opening a pull request puts the main agent on it; the main agent merges once every gate holds,
   without a separate go-ahead in chat. Subagents hand their pull requests back and never merge.
3. Argus reviews the final head once before the merge; after a repair that changes logic it reviews again. Because
   Argus closes a thread answered by a commit only when it reviews again, the procedure asks for another review after
   any commit that answers a finding; a reply alone is judged at once and needs none.
4. Argus resolves its own review threads. Agents do not resolve them unless they verified that Argus is
   malfunctioning or a person allowed it. `guard.ps1 pre-merge` counts only unresolved threads.
5. On GitHub, the default and integration branches of Mercury, Argus, Ship of Theseus, the SoT design library and
   Pokemon Dungeon Lab block force pushes and deletion, require a pull request (zero approvals) and require the CI
   checks that run on every pull request. Administrators are not bound (`enforce_admins` off); agents never use
   `--admin`.

## What it replaces

The merge rule of #632, #636 and #672: a user confirmation in chat for every merge, Argus excluded from review, and
a native-review merge that required zero review threads, resolved ones included.

## Where it lives

The procedure is `packs/git-safe/docs/pr-procedure.md`; Mercury applies it in
[git-flow.md](../02-guides/git-flow.md#pr-review-and-merge). Argus's side (comment-triggered reviews, thread
resolution) is described in the Argus repository's README.
