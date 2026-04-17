# mem0 Memory Layer — Setup (Phase A)

Phase A deliverable for [#252](https://github.com/392fyc/Mercury/issues/252).
Research basis: `.mercury/docs/research/memory-layer-rebuild-2026-04-16.md`.

## Scope

Phase A ships only the adapter, migration, and smoke-test.
**Hooks are NOT wired yet** — that is Phase B (#252).

## Files

| File | Purpose |
|---|---|
| `scripts/mem0_hooks.py` | Adapter with 4 mandatory P1-bug guards |
| `scripts/mem0_migrate.py` | One-shot AgentKB Markdown -> mem0 importer |
| `scripts/mem0_smoke_test.py` | Regression test for every guard |
| `requirements-mem0.txt` | Pinned deps (mem0ai 1.0.11+, qdrant-client 1.9+) |

## One-time install

Bash (Git Bash / MSYS2):

```bash
python -m venv .venv-mem0
. .venv-mem0/Scripts/activate
pip install -r requirements-mem0.txt
```

PowerShell:

```powershell
python -m venv .venv-mem0
. .\.venv-mem0\Scripts\Activate.ps1
pip install -r requirements-mem0.txt
```

Required env vars (register in global `settings.json` once Phase B wires live hooks).

Bash:

```bash
export MEM0_TELEMETRY=false
export ANONYMIZED_TELEMETRY=false
export OPENAI_API_KEY=sk-...
```

PowerShell:

```powershell
$env:MEM0_TELEMETRY         = 'false'
$env:ANONYMIZED_TELEMETRY   = 'false'
$env:OPENAI_API_KEY         = 'sk-...'
```

Optional overrides:

- `MERCURY_MEM0_QDRANT_PATH` — Qdrant on-disk storage (default `.mercury/state/mem0/qdrant`)
- `MERCURY_MEM0_HISTORY_PATH` — SQLite history DB (default `.mercury/state/mem0/history.db`)
- `MERCURY_MEM0_CONFIG` — JSON path overriding the full `Memory.from_config` dict. **The path must live inside the repo directory** (enforced via `Path.relative_to(repo_root)`); paths outside the repo are ignored with a `stderr` warning and defaults are used. This is a deliberate security clamp — see commit `56d28b3` — so untrusted env vars cannot redirect reads to arbitrary filesystem locations.

Default paths live under `.mercury/state/` (git-ignored; chosen over `/tmp/qdrant` because that path is broken on Windows).

## Smoke test

```bash
python scripts/mem0_smoke_test.py
```

Exit 0 = all four P1-bug guards hold (#4099 empty-payload, #4453 threshold absent, #4536 dedup, #4799 list-coerce).
Non-zero = regression — do NOT proceed to Phase B.

## AgentKB migration (dry-run first)

```bash
python scripts/mem0_migrate.py --source "$AGENTKB_DIR" --dry-run
python scripts/mem0_migrate.py --source "$AGENTKB_DIR"
```

Source Markdown is never modified (additive migration; rollback = ignore mem0 store).

## Adoption preconditions (MUST remain true)

From research doc §Q2:

1. Single-user runtime only.
2. Never pass `threshold=` to `Memory.search()` — `search_safe` enforces.
3. Content must be a plain string; list-shaped input is coerced by `add_safe`.
4. LLM is not Gemini-3 (Claude / GPT only).
5. Telemetry opt-out enforced via env vars above.

If any precondition is violated in future work, re-derive the choice before continuing.
