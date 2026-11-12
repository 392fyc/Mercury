# Context continuity in long Claude Code sessions

How rules and settled decisions stay in effect through compaction (#777). The measurements and sources behind the
numbers are in [context-compaction-2026-11.md](../04-research/mercury/context-compaction-2026-11.md).

Compaction replaces the conversation with a summary. Afterwards Claude Code reloads the project-root `CLAUDE.md`, but
a file the agent read earlier comes back only if it is among the five most recently modified and under 5,000 tokens.
A rule table or a preferences file read at session start is therefore gone after the first compaction, and on this
setup most compactions are idle compactions: by default Claude Code compacts a session over 200K tokens that has
been left for about an hour.

## What is set up

Both parts are user-level configuration, outside this repository. Changes to them are recorded in a Mercury Issue,
not delivered by pull request.

| Part | Setting | Why |
|---|---|---|
| Auto-compact window | `autoCompactWindow: 400000` in the user settings file | The default for a 1M-context model is about 967K. A window saved for one model with `/autocompact <value>` takes precedence over this key for that model. Start a new session after changing it: a user report says a running session keeps its old value |
| Idle compaction | default (on, 200K minimum) | Turning it off makes every return to a long session reprocess the whole uncached context |
| SessionStart hook | runs on every source (`startup`, `resume`, `clear`, `compact`, `fork`) and injects the briefs that apply | Puts the rules back after each compaction |

## Briefs

A brief is one short page, `brief.md`, that a project keeps so that it can be injected. It holds what must not be
forgotten in the middle of work, and nothing else.

| Brief | Location | Injected when |
|---|---|---|
| Project brief | the project's bucket in its memory root | on every source |
| Lane brief | `lanes/<name>/` in that bucket | the transcript's first user message starts with `[LANE=<name>]` (a name of up to 64 letters, digits, `_` or `-`) and that lane has a `brief.md` |

A project that keeps its memory in its own main checkout, as the lanes pack does, uses these locations instead. The
hook walks up from the working directory to the nearest `.mercury/memory/`; memory is ignored by Git, so from a task
worktree under `.worktrees/` the walk ends at the main checkout.

| Brief | Location | Injected when |
|---|---|---|
| Project brief | `.mercury/memory/brief.md` | on every source |
| Lane brief | `brief-<name>.md` in the lane directory that `.mercury/memory/lane-home` names, next to that lane's inbox | as above |

Limits of the lane brief as wired today:

- It is wired for those two layouts. Mercury's own lanes get the project brief only.
- The hook finds the lane in the transcript, so the lane brief arrives on `compact` and `resume`, not on `startup`
  (nothing has been said yet) or after `/clear` (a new transcript). The starting prompt or handoff of a lane session
  therefore still names the lane brief as a file to read first; the hook brings it back after each compaction.
- Only the first message counts. A session whose first message carries no tag, or is a system notice, has no lane.

Rules for the pages:

- **Size.** All briefs injected into one session share 6,000 characters. The count is in characters, not bytes: a
  Chinese character counts one, although it takes three bytes in the file, so `wc -c` (and `wc -m` in a shell
  without a UTF-8 locale) reports about three times the real size of a Chinese page. Measure with
  `python -c "import sys;print(len(open(sys.argv[1],encoding='utf-8').read().strip()))" <file>`, or read the
  count the hook prints beside each injected page. The hook never cuts a page: a page that does
  not fit is left out, and the session is told to read the file and to tell the user that the page needs trimming.
  If the hook's whole output would still pass 9,500 characters (very long paths), it injects no page body and names
  the files to read instead.
- **Current state only.** Write what holds now. When a decision replaces an earlier one, change the line and delete
  the old one; do not stack dated sections that each claim priority. History stays in Git, Issues and the KB.
- **Project brief:** the user's standing preferences for the project and the few rules whose breach the user has had
  to correct more than once. Point to the full rule document for the rest.
- **Lane brief:** what the lane is working on, the decisions in force for that work with their reasons, what is
  deferred and until when, and the next step.
- **Say whose words they are.** Mark a line as the user's decision or as the agent's inference. A later session
  cannot tell them apart otherwise.
- **Update at the moment of the decision.** When the user settles something, the session writes it into the lane
  brief before going on. After the next compaction, this page is what the session still knows.
- A brief is memory, not authority: design facts and decisions of record stay in the design library and the KB.

A project without a `brief.md` gets the previous behavior: the hook prints where the memory files are.

## The other lane pages

A brief only helps if the pages around it stay readable. A lane's checkpoint and lane page are read at every start, so
they follow the same rule as a brief: current state only, about a page. Where the start hook knows the lane's
directory it checks two things and, when one is over, tells the session to tell the user and to tidy up at a natural
pause:

| Checked | Limit (working values) |
|---|---|
| `session-checkpoint.md` and `LANE.md` of the session's own lane | 6,000 characters each, the size of the brief budget |
| Files directly in the lane's directory | 30; receipts, evidence and review requests belong in the lane's work area |

Both checks run for the layout with one directory per lane under the project bucket, for a session whose first
message names its lane. In Mercury's own bucket only the project checkpoint's size is checked. In the layout that
keeps memory in the project's main checkout nothing is checked yet: a lane's pages there sit in places the project
chooses, so the rule holds by the lane contract alone.

Two rules go with the tidying, and the notice repeats them:

- **Tidying does not reopen anything.** An old open item is checked against the KB first. What has an answer is
  recorded with where the answer is; what the user said to do later is recorded as deferred, with the user's words
  and the condition for taking it up; what the rules leave to the agent is decided and labeled as inference. None of
  these becomes a question to the user. The first rewrites of long checkpoints did the opposite: they turned every
  old pending line into "please confirm", including matters the KB had settled and matters the user had deferred.
- **Own lane only.** A session tidies its own lane's pages. Other lanes' pages can be read, never written.

## Checking that it works

- **Window:** in a new session, `/autocompact` shows the window in effect. In a transcript, `compactMetadata.preTokens`
  on a `compact_boundary` entry shows where a compaction fired.
- **Injection:** after a compaction the transcript has a `hook_success` attachment named `SessionStart:compact`; its
  output contains the brief text.
- **Too long:** a page left out for size is announced in the session with its path and length.

## What this does not do

- It does not re-inject on every turn. The briefs are injected once per start or compaction and drift away again as
  the context grows.
- It does not replace handoff. A new session for a new batch of work still starts from a handoff; the handoff can be
  short when the lane brief is current.

## Codex

The same SessionStart hook script is installed for Codex and injects the same briefs; the two hosts share one
`brief.md` per project and lane.

- **Compaction threshold: unchanged.** Codex already compacts at about 95% of the model's window (measured: mostly 237K to
  248K on 258,400-token models and 443K to 452K on 475,000-token models, with a few outliers), so there is nothing to lower.
- **Injection limit:** Codex measures a hook's `additionalContext` in tokens; the limit for this hook is set to 10,000
  so that 6,000 mostly Chinese characters fit.
- **Lane:** Codex opens a session with user-role messages the user did not type (the AGENTS.md instructions, tagged
  notices from the app). The hook skips them and takes the tag from the first message that remains.
- **Verified:** after a manual `/compact` the hook's message appears again before the next user message (Codex
  0.167). Automatic compaction in the middle of a turn was not exercised.
- Codex skips a hook whose definition changed until it is trusted again with `/hooks`, and a running session keeps
  the hooks it started with.
