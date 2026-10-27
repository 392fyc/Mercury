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

## Mercury roles and Git safety rules

- Mercury installs five roles for both hosts: `mercury-dev`, `mercury-reviewer`, `mercury-acceptance`, `mercury-design` and `mercury-research`, as `.claude/agents/mercury-*.md` and `.codex/agents/mercury-*.toml`. They are generated from `.mercury/packs/core/roles/`; change the source in Mercury, not the generated files. A project role of the same name in `.claude/agents/` takes priority over a user-level one, so these project roles apply in this project.
- `mercury-reviewer`, `mercury-acceptance`, `mercury-design` and `mercury-research` are read-only. Codex enforces this with the sandbox; the Claude roles run a `PreToolUse` guard (`.mercury/packs/core/hooks/readonly_guard.sh`) that blocks write commands in Bash and PowerShell. The guard is best-effort, not an operating-system sandbox. A project can name more write-type scripts in `params.guard_write_scripts` in `.mercury/config.json`.
- `.codex/rules/mercury-git-safety.rules` forbids raw `git push` and direct switching to `develop`, `main` or `master` in Codex. Perform Git writes through the controlled entry point that this project's own instructions name; never bypass a denial through another shell.
