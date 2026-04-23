---
name: kb-lint
description: |
  Run AgentKB knowledge base health checks (lint) — detects broken links, orphan pages, stale articles, contradictions. **Run this regularly to keep KB healthy, especially before major refactors or content merges.** Requires `$AGENTKB_DIR` env var. Triggers: 'lint KB', 'KB health check', '知识库检查', 'check KB', 'KB lint', 'knowledge base health'. Offers `/kb-lint structural` (fast) or `/kb-lint full` (with LLM contradiction detection).
user-invocable: true
allowed-tools: Bash, Read
---

# KB Lint Skill

Runs the AgentKB `lint.py` health checker against the knowledge base.

## Prerequisites

The environment variable `AGENTKB_DIR` must be set to the AgentKB vault root directory.
If not set, abort with: `"ERROR: AGENTKB_DIR not set. Export it in your shell profile."`

## Commands

### `/kb-lint` or `/kb-lint structural`

Run structural checks only (fast, free, no LLM cost):

```bash
uv run --directory "$AGENTKB_DIR" python "$AGENTKB_DIR/scripts/lint.py" --structural-only
```

### `/kb-lint full`

Run all checks including LLM contradiction detection (costs API tokens):

```bash
uv run --directory "$AGENTKB_DIR" python "$AGENTKB_DIR/scripts/lint.py"
```

## After Running

1. Read the generated report at `$AGENTKB_DIR/reports/lint-YYYY-MM-DD.md`
2. Present the results to the user in a summary table:
   - Errors (must fix)
   - Warnings (should fix)
   - Suggestions (nice to have)
3. If orphan sources found → suggest running compile: `uv run --directory "$AGENTKB_DIR" python "$AGENTKB_DIR/scripts/compile.py"`
4. If errors > 0 → flag as needing immediate attention

## 7 Checks Performed

| # | Check | Severity | Description |
|---|-------|----------|-------------|
| 1 | Broken links | error | `[[wikilinks]]` pointing to non-existent articles |
| 2 | Orphan pages | warning | Articles with zero inbound links |
| 3 | Orphan sources | warning | Daily logs not yet compiled |
| 4 | Stale articles | warning | Source logs changed since last compilation |
| 5 | Missing backlinks | suggestion | A→B link exists but B→A does not |
| 6 | Sparse articles | suggestion | Articles under 200 words |
| 7 | Contradictions | warning | LLM-detected conflicting claims (full mode only) |

## Paths

All paths are relative to `$AGENTKB_DIR`:
- Scripts: `scripts/lint.py`
- Reports output: `reports/`
- Knowledge base: `knowledge/`
