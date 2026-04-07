---
name: gh-project-flow
description: |
  BOOTSTRAP-ONLY task management for Mercury self-development via GitHub Project #3. Lets the main agent pull the next Phase + P0 Todo task, mark it In Progress, link work products (PR/Issue), and move items to Done. Use this skill when the user says "next task", "下一个任务", "拉任务", "认领任务", "标记 in progress", "project status", "更新 project", "Mercury 项目看板", "Phase 1 任务", "gh-project-flow". DO NOT use this skill for general (non-Mercury) project development — those scenarios will use Memory Layer (Phase 3) + Dev Pipeline (Phase 1 self-output) instead. This skill exists to bootstrap Mercury's own buildout and will be retired when Phase 3 lands.
user-invocable: true
allowed-tools: Bash, Read, Grep, Write, Edit
---

# gh-project-flow — Bootstrap Task Management for Mercury Self-Dev

> **BOOTSTRAP-ONLY**. This skill drives Mercury's *own* development task board (GitHub Project #3, owner `392fyc`). It is not a general-purpose project tracker. Mercury's vision for general dev is Memory Layer + Dev Pipeline, which arrive in Phase 3. When that ships, this skill is deprecated.

## When This Applies

- The user is working on Mercury itself and asks for the next task
- A new Mercury Phase milestone needs to be picked up
- A PR / Issue created for Mercury work needs to be linked back to the project board
- Status of a Mercury Phase item needs to flip (Todo → In Progress → Done)

**Do NOT use** when the user is working in another repo, building a feature for an external project, or asking for general Kanban automation.

## Verified API Surface (2026-04)

Verified directly against the local `gh` CLI and against [GitHub CLI manual](https://cli.github.com/manual/gh_project_item-list):

| Command | Default `--limit` | Notes |
|---|---|---|
| `gh project item-list <N> --owner <login> --format json` | **30** | ⚠️ Mercury Project #3 has 37+ items. ALWAYS pass `--limit 100` (or higher) explicitly, otherwise Phase 1 P0 items are silently truncated. |
| `gh project field-list <N> --owner <login> --format json` | **30** | Project #3 currently has 12 fields, fits within default — but pass `--limit 100` defensively (matches the rule below). |
| `gh project item-edit --id <item-id> --field-id <field-id> --project-id <project-id> --single-select-option-id <option-id>` | n/a | Single-field-per-invocation. Issue items require `--project-id`; draft items don't. |
| `gh project view <N> --owner <login> --format json` | n/a | Returns project node ID (`PVT_*`) needed by `--project-id`. |

Sources:
- https://cli.github.com/manual/gh_project_item-list
- https://cli.github.com/manual/gh_project_item-edit
- https://cli.github.com/manual/gh_project_field-list

### JSON shape returned by `item-list`

`item-list --format json` returns `{items: [...], totalCount: N}`. Each item:

```jsonc
{
  "id": "PVTI_lAHO...",            // project item ID — pass to item-edit --id
  "title": "...",                   // resolved title (issue title or draft body)
  "status": "Todo",                 // resolved single-select VALUE (not option ID)
  "phase": "Phase 1",               // custom single-select VALUE
  "priority": "P0",                 // custom single-select VALUE
  "labels": ["enhancement"],
  "assignees": ["392fyc"],
  "content": {
    "type": "Issue",                // "Issue" | "DraftIssue" | "PullRequest"
    "number": 178,                  // null for DraftIssue
    "title": "...",
    "url": "https://github.com/...",
    "body": "...",
    "repository": "392fyc/Mercury"
  }
}
```

**Important**: `status`, `phase`, `priority` come back as the human-readable VALUE (e.g. `"Todo"`), not the option ID. To EDIT them, you must look up the option ID via `field-list` first.

## Mercury Project #3 Cached IDs

These are stable as of 2026-04-07. **The cache is fragile**: if Project #3 fields are recreated, deleted, or migrated, every ID below becomes garbage and `item-edit` will fail. Run the verify snippet at the bottom of this section before any high-stakes batch operation, and re-run `field-list` if `item-edit` ever returns "field not found".

```bash
PROJECT_NUMBER=3
PROJECT_OWNER=392fyc
PROJECT_ID=PVT_kwHOBiaNmM4BT4Nv

# Status field
STATUS_FIELD_ID=PVTSSF_lAHOBiaNmM4BT4NvzhBEE4c
STATUS_TODO=f75ad846
STATUS_IN_PROGRESS=47fc9ee4
STATUS_DONE=98236657

# Phase field
PHASE_FIELD_ID=PVTSSF_lAHOBiaNmM4BT4NvzhBEE_o
PHASE_0=3332c598
PHASE_1=23142fd5
PHASE_2=2b168873
PHASE_3=1dfe92b6
PHASE_4=befb60c6
PHASE_5=60c7cd9b

# Priority field
PRIORITY_FIELD_ID=PVTSSF_lAHOBiaNmM4BT4NvzhBEFA4
PRIORITY_P0=a2c3f4ac
PRIORITY_P1=3d4d12f9
PRIORITY_P2=e555ac5d
```

To regenerate the cache from scratch:

```bash
gh project field-list 3 --owner 392fyc --format json --limit 100 | jq '.fields[] | select(.type=="ProjectV2SingleSelectField") | {name, id, options}'
gh project view 3 --owner 392fyc --format json --jq '.id'
```

To **verify** the cache against current Project state (run this before any batch operation that depends on the IDs):

```bash
# Compare cached IDs against live values; nonzero exit means drift.
# tr -d '\r' guards against CRLF when running under git bash on Windows.
LIVE=$(gh project field-list 3 --owner 392fyc --format json --limit 100 \
  | jq -r '.fields[] | select(.type=="ProjectV2SingleSelectField") | "\(.name)=\(.id)"' | tr -d '\r' | sort)
EXPECTED=$(printf '%s\n' \
  "Status=PVTSSF_lAHOBiaNmM4BT4NvzhBEE4c" \
  "Phase=PVTSSF_lAHOBiaNmM4BT4NvzhBEE_o" \
  "Priority=PVTSSF_lAHOBiaNmM4BT4NvzhBEFA4" | sort)
diff <(echo "$LIVE") <(echo "$EXPECTED") && echo "cache OK" || { echo "DRIFT — regenerate cache"; exit 1; }
```

## Operation 1 — `next-task`

Pull the next P0 Todo item for a given Phase. Caller passes the Phase explicitly; this skill does NOT auto-detect "active" phase (that's a workflow decision belonging to Main, not the skill).

```bash
PHASE="${1:-Phase 1}"
PRIORITY="${2:-P0}"

# NOTE: gh --jq does NOT accept --arg. Pipe to standalone jq to pass shell variables safely.
# Canonical: filter by phase + status + priority explicitly. Do NOT rely on sort_by for priority semantics.
gh project item-list 3 --owner 392fyc --format json --limit 100 \
  | jq --arg phase "$PHASE" --arg pri "$PRIORITY" '
    .items
    | map(select(.status=="Todo" and .phase==$phase and .priority==$pri))
    | .[0]
    | {
        id,
        title,
        priority,
        phase,
        type: .content.type,
        number: .content.number,
        url: .content.url
      }
  '
```

Returns one item or `null`. Issue items have a `number` and `url`; draft items have `null` for both. To fall back to lower priorities when no P0 remains, call again with `PRIORITY=P1`, then `P2`. The skill does NOT auto-fall-back — that is a workflow decision the caller owns.

To list all P0 Todo for a Phase (not just one):

```bash
gh project item-list 3 --owner 392fyc --format json --limit 100 \
  | jq --arg phase "$PHASE" '
    .items
    | map(select(.status=="Todo" and .phase==$phase and .priority=="P0"))
    | map({id, title, type: .content.type, number: .content.number})
  '
```

## Operation 2 — `start <item-id>`

Move an item Todo → In Progress.

```bash
ITEM_ID="$1"

gh project item-edit \
  --id "$ITEM_ID" \
  --field-id "$STATUS_FIELD_ID" \
  --project-id "$PROJECT_ID" \
  --single-select-option-id "$STATUS_IN_PROGRESS"
```

This works for both Issue items and Draft items because we pass `--project-id` either way (it's only optional for drafts, never harmful).

## Operation 3 — `link <item-id> --pr <N>` / `--issue <N>`

For **issue-backed items**: linking is automatic. When the PR's body or commit messages contain `Closes #N` / `Fixes #N` / `Resolves #N`, GitHub auto-moves the linked Issue's project item to Done on PR merge. No skill action needed.

For **draft items**: there's no native link. The skill convention is to convert the draft to a real Issue first if linking matters, OR record the PR number in the draft body via `gh project item-edit --title` or by editing the draft body. Recommended: convert to Issue using:

```bash
# Read draft body, then create real Issue, then archive draft.
ITEM_ID="$1"
gh project item-list 3 --owner 392fyc --format json --limit 100 \
  | jq --arg id "$ITEM_ID" '.items[] | select(.id==$id) | .content'
# Then `gh issue create` with that body, then add the new issue to the project.
```

(In practice for Phase 1 we will lift each draft into a real Issue before starting work, so this conversion path stays rare.)

## Operation 4 — `done <item-id>`

For **issue items**: prefer letting `Closes #N` in the merged PR auto-move it. Manual override only if the PR was merged without the keyword:

```bash
gh project item-edit \
  --id "$ITEM_ID" \
  --field-id "$STATUS_FIELD_ID" \
  --project-id "$PROJECT_ID" \
  --single-select-option-id "$STATUS_DONE"
```

For **draft items**: always manual (drafts have no PR link). Use the same `item-edit` invocation above.

## Iron Rules

1. **Always pass `--limit 100`** to `item-list`. Default 30 silently truncates Mercury's 37+ items.
2. **Never invent option IDs**. Use the cached values above; if they fail, regenerate via `field-list`.
3. **One field per `item-edit` call**. The CLI does not support multi-field updates in one invocation.
4. **Issue-first**. When practical, convert draft items to real Issues before starting work — that's the only way `Closes #N` automation kicks in. The full Mercury workflow expects an Issue per task.
5. **BOOTSTRAP-ONLY scope**. Do not extend this skill to support arbitrary projects, users, or non-Mercury repos. If general project automation is needed, build it in `dev-pipeline` or wait for Phase 3.

## Windows Path Mangling

`gh project` subcommands take no `/`-prefixed arguments, so `MSYS_NO_PATHCONV=1` is **not** required for any command in this skill. If you ever pipe through `gh api /...`, prefix with `MSYS_NO_PATHCONV=1`.

## Failure Modes

| Symptom | Cause | Fix |
|---|---|---|
| `next-task` returns null but you expect items | Default `--limit 30` truncated past your phase | Confirm `--limit 100` is in the command |
| `item-edit` errors `field not found` | Cached field ID out of date | Re-run `field-list` and update the cache section above |
| `Closes #N` didn't move item to Done | PR didn't actually contain the keyword, OR the linked Issue isn't in this project | Verify both, then manual `done` |
| Draft item shows `content.number: null` | Expected — drafts have no issue number | Convert to Issue if linking needed |
| Multiple items returned by `next-task` query | jq filter missing `.[0]` | Use the canonical filter above |
