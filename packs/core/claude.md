@AGENTS.md

## Claude Code with Mercury

The AGENTS.md above holds the shared project rules, including the Mercury-managed section. Its assignments of the Codex host, Codex tools and Codex models apply to Codex only; do not infer Claude Code tool, permission or model capabilities from them.

- Mercury installs the roles `mercury-dev` and `mercury-research` on Sonnet (alias `sonnet`), and `mercury-reviewer`, `mercury-acceptance` and `mercury-design` on Opus (alias `opus`), in `.claude/agents/`. They set no `effort`; the default applies.
- Read-only roles disable Edit, Write and NotebookEdit through the native `disallowedTools` and run the write guard described in AGENTS.md. Neither is operating-system isolation; Bash remains subject to the current host permissions. Role descriptions are behavioral guidance and grant no extra permissions.
- Without workspace trust, project agent frontmatter hooks do not run, so the guard is inactive; sessions started with `-p` are never trusted. Accept the trust dialog for the folder before relying on the guard.
- When the parent session runs in bypassPermissions, acceptEdits or auto mode, a subagent's `permissionMode` is ignored, so `disallowedTools` plus the guard are what enforce read-only. A user-level `PreToolUse` hook that keys on the agent type in the hook payload can back the guard up where project hooks do not run.
