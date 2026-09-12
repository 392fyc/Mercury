# Mercury — Codex working contract

## Execution

- Follow `.mercury/docs/DIRECTION.md` for development decisions. Keep Mercury thin, detachable, and compatible with stronger models; prefer native capabilities and suitable external projects.
- Default to Codex autonomous execution. Use focused skills or bounded delegation for a specific need. Invoke complex workflows, multi-stage pipelines, or dual-verify only when the user explicitly calls for them or an established task plan requires them; a keyword mention alone does not start a workflow.
- Implementation agents inherit the calling task's model and reasoning settings unless the task explicitly selects an override. Other roles retain their configured settings. Codex role definitions live in `.codex/agents/`; `.claude/agents/` serves the separate Claude Code environment.
- Before repository changes, associate the work with a GitHub Issue. PRs must reference it with `Closes`, `Fixes`, `Resolves`, or `Refs`. Record meaningful delivery evidence on the Issue when issue updates are authorized.
- Match verification to risk. Run relevant checks and inspect the final diff. Obtain independent review for substantial behavior changes, cross-repository writes, security, permissions, or agent instruction/rules changes. Small, reversible edits need the smallest meaningful checks. Report skipped checks and unresolved findings accurately.
- Commit at coherent delivery points when the task calls for Git delivery. Workers commit or push only when assigned, using the guarded sequence below.
- Reply in clear, complete Simplified Chinese. Keep useful technical names unchanged; code, commit messages, and PR bodies follow their existing conventions.

## Git and local safety

- Keep unrelated changes. Stage explicit files only. Do not reset, stash, rewrite history, force-push, or remove other work to simplify a task.
- Use task branches accepted by `scripts/codex/guard.ps1`: `feature/TASK-<id>` or `lane/<lane>/(init|<n>|<n>-<slug>)`. Never commit or push directly to `develop`, `main`, or `master`. Merge into `develop` through a PR with required approval and checks.
- For Git writes, use `powershell -File scripts/codex/git-safe.ps1 add/commit/push`, including outside Codex. Before commit: stage intended files, inspect that staged diff, complete required checks and review, then run `powershell -File scripts/codex/guard.ps1 mark-review`. Changed staged content needs renewed review and a fresh mark. The mark records a snapshot, not review evidence.
- Preserve the sandbox and command rules. Request scoped access when needed; never bypass a denial through another shell. Use `.codex/rules/` and guarded scripts. This project has no hook registrations; check current official documentation before changing runtime wiring.
- On Windows, install software under `D:\Program Files`. Keep machine paths and local state out of portable templates.
- Back up user-level configuration, hooks, or scripts before changing them and leave rollback instructions. Track that work in a Mercury Issue with commands, a diff summary, and verification evidence; user-level files do not use the Mercury PR delivery path.
- Never publish credentials, tokens, private memory, or secret-matching text in commits, Issues, receipts, or migration inventories. Describe sensitive findings without copying their contents.

## Ownership and memory

- Mercury owns its harness and `.mercury/templates/codex-project/` source. SoT owns its game code, domain rules, and overlays. KB and design-library writes require their own authorized scope.
- Downstream files declared by the template manifest are generated. Read the template README and use `scripts/codex/sync-project-template.py` check/apply from a recorded Mercury commit with its authenticated lock. Do not hand-edit generated SoT files or overwrite downstream overlays.
- For prior context, start with `.mercury/memory/README.md` when present and load indexed entries on demand. This is local private memory, excluded from the public repository. Protected archives and chat records are not active memory.
- Mercury_KB remains active; its configured location is in `.handoff-config`. Consult `.mercury/docs/guides/kb-structure.md` when working with KB structure.

## External dependencies and imports

- Before SDK/API code or package-version claims, verify vendor documentation through web search; verify package versions and publication status in the relevant registry. Cite the supporting sources. Source repositories alone are insufficient; mark unverified claims explicitly if verification is unavailable.
- External integrations use `adapters/<vendor>/` and stay under 200 lines. Internal `scripts/` and `mercury-gui/` tooling is exempt from that adapter limit. Features and modules must remain independently detachable.
- Default to pinned Git submodules under `modules/`. Runtime-only `uvx git+SHA` and exact-version npm MCP packages are allowed with a permissive license, `.mercury/state/upstream-manifest.json` entry, `adapters/<vendor>/UPSTREAM.md`, and drift monitoring. Never use floating npm versions for these mounts.
- In agent-context files, escape tool-call XML markers with `&lt;` and `&gt;` rather than writing literal tags. Run `scripts/toolcall-xml-lint.sh` for relevant changes.

## Upstream file imports

Before copying files from an external project, verify a permissive license (MIT,
Apache-2.0, or equivalent) and the exact source SHA through
`gh api repos/{owner}/{repo}/commits/{sha}`. Do not record an unverified SHA.
The same commit must include these records as applicable to the imported files:

1. `.mercury/state/upstream-manifest.json`: `path`, `scope` (`project` for repository files, `user` for global files), `upstream_repo`, `upstream_path`, `upstream_sha_at_import`, `upstream_license`, `import_pr`, `import_date`, `import_rationale`, and `last_drift_check` (initially `null`).
2. Skill frontmatter: `upstream_source`, `upstream_sha`, `upstream_license`, `cherry_picked_in`, and `cherry_picked_at`.
3. Scripts: a five-line attribution comment after the shebang with `UPSTREAM`, `SOURCE`, `SHA`, `DATE`, and `ISSUE`.
4. Config/template files: a top-of-file attribution comment in the form `Based on <upstream> (LICENSE) SHA: <sha>` using the file's comment syntax.

Run `scripts/upstream-drift-check.sh` for drift monitoring. Before applying this
protocol to CLI-generated scaffolding or registry imports, read
`.mercury/docs/guides/cherry-pick-carve-out.md`: only the listed generators and
eligible source paths qualify for its exceptions. Unlisted generators and
external file copies through local paths follow the full import protocol.

## References on demand

- Planning: `.mercury/docs/EXECUTION-PLAN.md`.
- Git details: `.mercury/docs/guides/git-flow.md`.
- Hook history: `.mercury/docs/research/codex-hooks-adoption-2026-05.md`.
- Migration history: `.mercury/docs/research/issue-571-codex-migration-2026-08.md` and Issue #571. Recheck version-specific runtime conclusions.

<!-- MERCURY_AGENTS_MD_TAIL_SENTINEL -->
