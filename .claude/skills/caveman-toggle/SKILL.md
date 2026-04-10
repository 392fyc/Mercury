---
name: caveman-toggle
description: |
  Toggle persistent caveman concise mode for Mercury. Manages CLAUDE.local.md to enable/disable
  terse output style across sessions. Use when the user says "/caveman-on", "/caveman-off",
  "/caveman-status", "开启caveman", "关闭caveman", "简洁模式", "caveman mode".
user-invocable: true
allowed-tools: Read, Write, Bash
---

# Caveman Toggle Skill

Manages `CLAUDE.local.md` at the project root to enable or disable caveman concise mode.
Based on [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) (MIT).

**Important**: CLAUDE.local.md is loaded at session start only. Changes take effect on the **next session restart**.

## Commands

### `/caveman-on [lite|full|ultra]`

Default intensity: `lite`.

1. Read `CLAUDE.local.md.example` from the project root
2. Validate the template contains exactly one `<!-- caveman-mode: ... -->` to `<!-- end-intensity -->` block; if not, abort with error: "Template block missing or duplicated — check CLAUDE.local.md.example"
3. If intensity is `full` or `ultra`, replace that single block with the appropriate variant (see intensity variants below)
4. Write the result to `CLAUDE.local.md` (overwrite if exists) only after successful validation/replacement
4. Print: "Caveman [intensity] mode enabled. **Restart session to activate.**"

### `/caveman-off`

1. Check if `CLAUDE.local.md` exists
2. If yes: delete it with `Bash: [ -n "$CLAUDE_PROJECT_DIR" ] && [ -f "$CLAUDE_PROJECT_DIR/CLAUDE.local.md" ] && rm -- "$CLAUDE_PROJECT_DIR/CLAUDE.local.md"`
3. Print: "Caveman mode disabled. **Restart session to deactivate.**"
4. If no: print "Caveman mode is already off."

### `/caveman-status`

1. Check if `CLAUDE.local.md` exists (use Bash: `test -f "$CLAUDE_PROJECT_DIR/CLAUDE.local.md" && echo exists || echo missing`)
2. If exists: grep `<!-- caveman-mode:` in `"$CLAUDE_PROJECT_DIR/CLAUDE.local.md"` to read current intensity
3. Print current state: "Caveman [intensity] — active (restart already done if instructions visible)" or "Caveman OFF"

## Intensity Variants

When the user requests `full` or `ultra`, replace the intensity block in the example:

**lite** (default — grammar preserved, filler removed):
```
<!-- caveman-mode: lite -->
Respond terse like smart caveman. Drop filler words, hedging, pleasantries.
Keep articles and full sentences. No "sure/certainly/happy to/I think/perhaps/basically".
<!-- end-intensity -->
```

**full** (drop articles, allow fragments):
```
<!-- caveman-mode: full -->
Respond terse like smart caveman. Drop filler, articles, hedging, pleasantries.
Fragments ok. Short synonyms. Pattern: [thing] [action] [reason].
<!-- end-intensity -->
```

**ultra** (maximum compression):
```
<!-- caveman-mode: ultra -->
Respond terse like smart caveman. Drop filler, articles, conjunctions.
Abbreviate: DB/auth/config/req/res/fn/impl. Use X→Y for causality. Max brevity.
<!-- end-intensity -->
```
