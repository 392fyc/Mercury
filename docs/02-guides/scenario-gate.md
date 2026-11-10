# Scenario gate

A user-level hook that turns two requirements into something checked at a fixed point instead of something asked for
in an instruction file (#779). How the triggers were chosen and measured is in
[scenario-gate-triggers-2026-11.md](../04-research/mercury/scenario-gate-triggers-2026-11.md).

Why a hook: rules in `AGENTS.md` and `CLAUDE.md` are context, not enforcement. In #777 a research round gave numbers
without sources although the rules asking for verified facts were loaded; an independent reviewer and a second round
found the errors. The earlier reply-language case (#735) showed the same thing: a reminder injected on every prompt
did not hold, a check at the end of the turn did.

## The two baselines

| Baseline | What the hook checks itself | What an independent role checks |
|---|---|---|
| External lookup and data verification | The delivery has the sections `数值与来源`, `解释不了的现象`, `反面证据`. On Claude Code a web lookup is on record since the check was switched on; on Codex the reply carries a source link. A line `【无需联网】` plus a reason replaces the lookup. | Every conclusion is supported by the data and every number's source holds. The verdict line `RESEARCH-CHECK: pass` is recorded by the hook, not by the main agent. |
| Local KB, code and rules | The project's required reading for the path being changed was read in full since the last compaction or `/clear`, by the agent that acts. The delivery has the section `适用规则`. | The output does not violate the rules, checked against the rule text. Verdict line `RULES-CHECK: pass`. |

The verifier is a read-only role (`mercury-acceptance` or `mercury-reviewer`) that gets the conclusions or the output
and where the data or rules are, not the main agent's reasoning.

## The three scenarios

| Scenario | Switched on by | Stays on until |
|---|---|---|
| Research | Phrases the user really uses, anywhere in typed text (调研, 查证, 核实, 可行吗, 有没有办法, 怎么处理的 ...), or a prefix: `研究：`, `调研：`, `research:` | a delivery passes |
| Design | Phrases (先出方案, 出草稿, 重新设计, 重做 ...), a prefix (`设计：`, `design:`), or a prompt that starts with `[LANE=<name>]` for a lane the project lists as a design lane | a delivery passes; for a design lane, the whole session |
| Implement | Nothing to type: a file of a project that declares required reading is about to be modified | always on for such a project |

- Each switch-on shows the user one line with the phrase that triggered it. A prompt starting with `检查结束` switches
  the research and design checks off.
- Words inside a pasted block do not switch anything on, and neither does a prompt that starts with a tag (a notice
  from a background task or another session). The one exception is a `[LANE=<name>]` tag at the very start of a
  prompt, pasted or typed, so that a pasted handoff can open a design-lane session.
- In a design-lane session every delivery needs a new rules verification: each reply that is not marked as a
  progress note is checked as a delivery. Expect most replies there to be progress notes.
- While a check is on, a reply is either a progress note whose first line is `【进行中】`, or a delivery. A delivery that
  fails is sent back with the reason, at most three times per user prompt; after that it is let through with a warning
  shown to the user.
- Implement is a hard block: the modification is denied, with the unread files named, until they are read. It applies
  to the file tools (`Edit`, `Write`, `NotebookEdit`; on Codex `apply_patch` once enabled there).

## What a project provides

`.mercury/gate.json`, described in the core pack's `mercury-scenario-gate.md` (template next to it). Without the file
a project is not gated on modifications and the design check has no reading requirement.

The file holds a general list (`required_reading`, for every modification) and groups by path (`scoped_reading`:
these files before changing those paths). The groups exist because one list for a whole repository forced a bad
choice in the first projects that adopted the gate. Where several lanes share a repository, a long design rule table
binds whoever changes the design documents while the lane that implements code works from other sources: a single
list either made every code change re-read the design rules after each compaction or left code changes ungated. Where
one long rule page serves the whole project, it could only be split into a core page and topic pages at the price of
no longer enforcing the topic pages. With groups, each kind of change has its own list.

## Differences between the hosts

| | Claude Code | Codex |
|---|---|---|
| Web lookup | recorded by the hook | hosted web search does not pass through hooks; a source link in the reply is required instead |
| Reading | exact: the `Read` tool, whole file | approximate: a shell command that prints the whole file |
| Block on modifications | on | installed only after a live session has shown that reads are recorded |

## What it does not guarantee

- It proves form, that files were read and that a verification ran. It does not prove that the reading was used, that
  the verifier judged well, or that a verdict belongs to the final text.
- The phrases are precise, not complete: most requests are short continuations ("可以运行", "再做一批") that no wording
  rule can classify. Those are covered by the lane switch, by the implement block, or by typing a prefix.
- Writing a file through a shell command is not seen by the block, and a project can edit its own `gate.json`.
- Only the `WebFetch` and `WebSearch` tools count as a web lookup. A lookup through an MCP tool or a shell command
  (`curl`, `gh`) is not seen; the delivery then needs the `【无需联网】` line saying how it was checked.
- A project without the two verifier roles can only reach the warning path of a delivery check.

## Operating it

- The script, its registration and its state are user-level, outside this repository; changes are recorded in the
  Issue, with backups and rollback steps.
- State is one small JSON file per session and per record, removed after 30 days.
- Codex skips a hook whose definition changed until it is trusted again with `/hooks`, and a session reads its hooks at
  start: a change takes effect in a new session.
