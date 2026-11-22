## Mercury-managed section

Mercury generates this block from the selected packs in `.mercury/config.json`. Edit the pack sources and run
`python tools/mercury.py apply`. Do not edit installed files or this block by hand. The project owns the text outside
the markers. See `.mercury/packs/core/docs/mercury-harness-ownership.md` for ownership and the 24,000-byte
`AGENTS.md` authoring rule.

## Explanation and reasoning

All agents explain their work for a reader outside the task. Use plain words, define unfamiliar terms, and give
concrete evidence. When an explanation has a gap, check the source or mark it unresolved.
In research, design, and investigation, start from the goal, hard constraints, and verified facts. Check assumptions
against primary sources or direct observation and mark uncertainty. User decisions and authorization are
constraints. Restate both rules when delegating to an agent that does not load them.

## Scope, verification, and completion

Use `.mercury/packs/core/docs/mercury-task-contract.md` for scope, evidence, review, and completion.
Use the project's required reading and checks. Set a deadline before every wait, including delegated waits. End on
exit, failure, or the deadline, report the last state on timeout, and stop expired or unneeded background work before
reporting completion. Restart a wait only for a stated reason.

## Mercury roles and Git safety rules

`mercury-reviewer`, `mercury-acceptance`, `mercury-design`, and `mercury-research` are read-only. Codex subagents
inherit the parent sandbox and approval settings. Claude Code's role guards check shell commands and do not provide
OS isolation.
Change role and skill sources, not generated entries. The ownership guide covers local roles and guard configuration.
Use the project's controlled Git entry point. Do not run raw `git push`, switch directly to `develop`, `main`, or
`master` in Codex, or bypass a denial through another shell.

## Reporting a harness problem to Mercury

Follow the ownership guide's reporting steps. Search the source repository named in `.mercury/lock.json` first and
report a problem with an installed component in a matching Issue or a new `[downstream-feedback]` Issue. Report
it yourself and give the user the link. Do not work around the rule. That feedback Issue or comment is authorized.
Other repository writes need their own authorization. Keep credentials, machine paths, and private project content
out of public reports.
