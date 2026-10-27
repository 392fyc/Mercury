---
name: mercury-lane-handoff
description: "Generate a structured handoff document + ready-to-paste starting prompt for the next session, preserving context across session boundaries. **Use `/mercury-lane-handoff` whenever you reach context limits, need to hand off to a colleague, or want to continue in a fresh session.** Modes: `/mercury-lane-handoff` (manual mode, output prompt), `/mercury-lane-handoff auto` (auto-launch next session). Includes state summary, acceptance criteria, references — no manual reconstruction needed."
argument-hint: "[auto] [optional extra instructions for the next session]"
user-invocable: true
allowed-tools: Read, Write, Bash, Glob, Grep
---

# mercury-lane-handoff

Arguments: $ARGUMENTS

Use the value on the `Arguments:` line above wherever the entry mentions `\$ARGUMENTS` (empty when none were given).

This skill is installed by Mercury (pack `lanes`). The full skill lives in `.mercury/packs/lanes/skills/lane-handoff/`.

Read `.mercury/packs/lanes/skills/lane-handoff/SKILL.md` and follow it. Resolve every relative path it mentions against `.mercury/packs/lanes/skills/lane-handoff/`. This entry adds no permission and no separate workflow.
