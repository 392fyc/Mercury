## Mercury-managed section

This section, from the begin marker to the end marker, is installed and kept up to date by Mercury
(`python tools/mercury.py apply`) from the packs the project selects in `.mercury/config.json`. Do not edit it by
hand: a hand edit stops the next apply. Everything outside the markers belongs to the project and is never touched.
Paths Mercury manages, and who may change them, are listed in `.mercury/packs/core/docs/mercury-harness-ownership.md`.

## Explanation and reasoning

These rules bind the main agent and every subagent in both Codex and Claude Code, in every kind of task.

- **Explain the Feynman way.** When you explain, report, review, or hand off, write so that someone outside the task can follow it. Use plain words, define each term the reader may not know, build from what the reader already knows, and back each point with a concrete example or the actual evidence. If you cannot explain a point simply, treat it as a gap in your own understanding: return to the source and close the gap, or mark the point as unresolved. Do not cover it with jargon.
- **Reason from first principles in research, design, and investigation.** Start from the goal, the hard constraints, and facts verified in primary sources or by direct observation, and build the conclusion up from them. Treat conventions, analogies, existing implementations, earlier conclusions, and other agents' claims as hypotheses to check, not as premises. State the assumptions a conclusion depends on and label any that remain unverified. Check the assumptions the conclusion actually depends on, in proportion to risk; evidence that stays applicable under the evidence-reuse rules counts as verified fact. User decisions and authorization are constraints, not hypotheses.
- **Carry the rules into delegation.** When you delegate to an agent that does not load these instructions, restate both rules in the delegation prompt. Examples: Claude Code's built-in Explore and Plan agents, agents defined with `omitClaudeMd`, and agents working in another repository or directory that has its own instructions.

## Scope, verification, and completion

Use .mercury/packs/core/docs/mercury-task-contract.md for
bounded scope, test relevance, evidence reuse, provenance-check triggers,
review, and completion. Read its details when needed for these decisions;
it does not add a mandatory workflow or weaken governing requirements.

## Waiting on long-running work

These rules bind the main agent and every subagent in both Codex and Claude Code (Mercury #706). A local Claude Code session puts no time limit on background commands, so a wait whose condition never comes true runs until someone notices.

- **Every wait has a deadline set before it starts.** Wrap a shell wait loop or a follow command such as `tail -f` in `timeout <duration>`, give a PowerShell loop a `Get-Date` or `Stopwatch` deadline, or use a watch the host bounds itself (Claude Code `Monitor` with `timeout_ms`). To learn when a background command finishes, rely on the host's completion notice instead of a second loop that polls its output.
- **End the wait on the watched process's exit and on every failure state**, not on counting success lines. When the deadline passes, report that the work did not finish and what it last showed; do not simply wait again.
- **Delegate with the deadline, and clean up before reporting.** Put the deadline in the delegation prompt. Before you report completion, check the background tasks you and your subagents started and stop any that ran past its deadline or is no longer needed.
- In Claude Code every role Mercury generates runs a `PreToolUse` guard (`.mercury/packs/core/hooks/wait_guard.sh`) that blocks a Bash or PowerShell wait loop or follow command without such a deadline. It is a heuristic check, not a time limit, and it does not cover the main session; details are in the task contract.

## Mercury roles and Git safety rules

- Mercury installs five roles for both hosts: `mercury-dev`, `mercury-reviewer`, `mercury-acceptance`, `mercury-design` and `mercury-research`, as `.claude/agents/mercury-*.md` and `.codex/agents/mercury-*.toml`. They are generated from `.mercury/packs/core/roles/`; change the source in Mercury, not the generated files. A project role of the same name in `.claude/agents/` takes priority over a user-level one, so these project roles apply in this project.
- `mercury-reviewer`, `mercury-acceptance`, `mercury-design` and `mercury-research` are read-only. Codex enforces this with the sandbox; the Claude roles run a `PreToolUse` guard (`.mercury/packs/core/hooks/readonly_guard.sh`) that blocks write commands in Bash and PowerShell. The guard is best-effort, not an operating-system sandbox. A project can name more write-type scripts in `params.guard_write_scripts` in `.mercury/config.json`, and scripts that write only for some `-Action` values in `params.guard_write_actions`.
- A project's own roles and skills live in its project area: declared in `.mercury/local.json`, with role sources in `.mercury/local/roles/`. Mercury's installer (`tools/mercury.py apply`, run from a Mercury checkout) generates their `.claude/agents/`, `.codex/agents/` and skill entry files without the `mercury-` prefix; change the source or the manifest, never the generated files. Mercury updates do not touch this area.
- `.codex/rules/mercury-git-safety.rules` forbids raw `git push` and direct switching to `develop`, `main` or `master` in Codex. Perform Git writes through the controlled entry point that this project's own instructions name; never bypass a denial through another shell.
