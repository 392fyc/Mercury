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

Limits of the lane brief as wired today:

- It is wired only for the SoT layout, where each lane has a directory under the project bucket. Mercury's own
  lanes get the project brief only.
- The hook finds the lane in the transcript, so the lane brief arrives on `compact` and `resume`, not on `startup`
  (nothing has been said yet) or after `/clear` (a new transcript). The starting prompt or handoff of a lane session
  therefore still names the lane brief as a file to read first; the hook brings it back after each compaction.
- Only the first message counts. A session whose first message carries no tag, or is a system notice, has no lane.

Rules for the pages:

- **Size.** All briefs injected into one session share 6,000 characters. The hook never cuts a page: a page that does
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
- It does not cover Codex.
