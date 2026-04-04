---
name: kb-obsidian-access
description: Access Mercury_KB via Obsidian MCP tools, never via filesystem paths from project CWD.
category: TOOL_GUIDE
roles:
  - main
  - research
origin: IMPORTED
tags:
  - kb
  - obsidian
  - mcp
generation: 0
parent_skill_ids: []
total_selections: 0
total_applied: 0
total_completions: 0
total_fallbacks: 0
last_validated_at: 2026-04-04T00:00:00.000Z
---

# Obsidian KB Access Pattern

## Rule

Mercury_KB lives in an Obsidian vault, not in the project directory. Always use Obsidian MCP server tools to read and write KB files. Never construct filesystem paths from project CWD — the vault path is separate.

## Tool Naming

Obsidian MCP tools are prefixed with `mcp__obsidian__`:
- `mcp__obsidian__read_file` — read a KB file
- `mcp__obsidian__create_or_update_file` — write a KB file
- `mcp__obsidian__search` — keyword search across KB

All paths are vault-relative, e.g. `Mercury_KB/04-research/RESEARCH-FOO.md`

## Task Bundle Path

The task bundle JSON is at vault-relative path: `10-tasks/<taskId>.json`

## Anti-Patterns

- Do NOT use the Read tool or readFileSync with Mercury_KB paths
- Do NOT assume vault path equals project CWD
- Do NOT write KB files without the correct vault-relative path prefix
