# GitHub Issues Workflow

GitHub Issues is the single source of truth for all task tracking in Mercury.

## Rules

1. **Every task starts as an Issue** — no work without an Issue number
2. **PRs must reference Issues** — use `Closes #N` (auto-close on merge) or `Refs #N` (manual close)
3. **Agent progress updates** — post comments on the Issue at milestone completion
4. **No agent-memory-only tasks** — if it's worth doing, it's worth an Issue

## Label Taxonomy

### Priority (mutually exclusive)
| Label | Color | Use when |
|-------|-------|----------|
| `P0` | #B60205 | Production down, data loss, security vulnerability |
| `P1` | #D93F0B | Blocks current sprint, high-impact bug or feature |
| `P2` | #FBCA04 | Important but not blocking, scheduled for next sprint |
| `P3` | #0E8A16 | Nice to have, backlog |

### Type (one per issue)
| Label | Color | Use when |
|-------|-------|----------|
| `bug` | #d73a4a | Something is broken |
| `enhancement` | #a2eeef | New feature or improvement to existing |
| `strategic` | #FF6600 | Long-term, high-impact initiative |
| `workflow` | #5319e7 | Process and workflow improvements |
| `research` | #0E8A16 | Investigation or evaluation task |

## Enforcement

- `pr-create-guard.sh` blocks PRs without `--assignee`, `--label`, `--base develop`, and Issue reference (`Closes #N` / `Refs #N`)
- CLAUDE.md MUST rule: "Issue-first workflow"
- Agents post milestone comments via `gh issue comment`

## Agent Workflow

```
1. Check for existing Issue (gh issue list)
2. If none: create Issue (gh issue create --title "..." --label "..." --assignee ...)
3. Create branch per git-flow guide (feature/TASK-XXX or fix/issue-N-description)
4. Work, commit, push
5. Post progress comment: gh issue comment N --body "Phase X complete: ..."
6. Create PR with Closes #N or Refs #N in body
7. After merge: Issue auto-closes (if Closes) or manually close
```
