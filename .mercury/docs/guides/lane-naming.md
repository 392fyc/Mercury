# Lane Naming + Capacity — `feedback_lane_protocol.md` Rule 2 & HARD-CAP

Implements the **Rule 2 short branch prefix** delta (v0.1 Delta 6, Issue
[#313](https://github.com/392fyc/Mercury/issues/313)) and the **HARD-CAP at 5
active lanes** delta (v0.1 Delta 7, Issue
[#314](https://github.com/392fyc/Mercury/issues/314)).

## Why two deltas in one guide

Both shape the lane registry: Δ6 controls how branches are named, Δ7 controls
how many lanes can exist concurrently. Operators reason about "what lane do I
open / what do I name its branch" in one mental motion; one combined guide is
shorter than two cross-referenced ones.

## Δ6 — Short branch prefix (`lane/<short>/<N>-<slug>`)

### What changed

- **OLD prefix** (still valid for backward-compat): `feature/lane-<lane>/TASK-<N>-*` — 45-65 chars
- **NEW prefix** (preferred for new work): `lane/<short>/<N>-<slug>` — ≤40 chars

Example: `feature/lane-side-multi-lane/TASK-313-314-phase-c` (51 chars) →
`lane/side-mlane/313-phase-c` (27 chars).

### Why short matters

- Community soft cap is ~50 chars (Graphite naming guide); LeanTaaS hard cap is
  28 chars. 65-char branches push past both, breaking IDE autocomplete + URL
  pasting + `gh pr` shell expansion in some terminals.
- Mercury empirically observed in S3-S5 that the legacy 51-char prefix already
  truncates in `git branch` listing on narrow terminals and forces line wraps
  in PR titles.

### Short-name convention

Each lane declares a `Short name` field in its own `LANES.md` section. The
short name MUST be:

- ≤ 8 characters (giving the rest of the branch ≥ 27 chars for `<N>-<slug>`)
- Match `[a-z0-9-]+` (lowercase + digits + hyphen only)
- Globally unique across all active + closed lanes (avoid colliding with a
  closed lane's archived branches)

Default mapping for current Mercury lanes:

| Lane name | Short name | Rationale |
|-----------|-----------|-----------|
| `main` | `main` | Already short; canonical default lane |
| `side-multi-lane` | `side-mlane` | Compress "multi-lane" → "mlane" (8 chars exact) |

For new lanes: pick a short name at lane open time and write it in the lane's
`LANES.md` section before any branch is created. If two operators independently
pick the same short name, lane-claim semantics + manual review apply (no
mechanical enforcement; collision is rare).

### Backward compatibility

- All existing `feature/lane-<lane>/...` branches and `feature/TASK-<N>-*`
  legacy main-lane branches REMAIN valid until their containing lane closes.
- New work on existing lanes MAY continue using the legacy prefix to avoid
  mid-lane branch-naming churn.
- New lanes opened after Δ6 SHOULD use the short prefix.

### Script support (current state)

`scripts/lane-sweep.sh` and `scripts/check-main-idle.sh` currently glob
`refs/heads/feature/lane-<lane>/*` for branch-activity probing. These scripts
will be extended in v0.2 to also glob `refs/heads/lane/<short>/*` once one
real lane uses the new prefix end-to-end (deferred to avoid speculative code).

If a lane ONLY has new-prefix branches and no legacy `feature/lane-*`
branches, the sweep's branch-activity signal will report "inf" until v0.2 ref
glob extension lands. This degrades gracefully — handoff mtime + Issue
activity remain valid signals, and the AND-gate verdict still requires three
stale signals before flagging stale.

## Δ7 — HARD-CAP at 5 active lanes

### Cap value

`LANES.md` MUST NOT exceed **5 active lanes** simultaneously. The cap is
declared in `feedback_lane_protocol.md` and enforced advisorily by
`scripts/lane-cap-check.sh`.

### Why 5

Three converging research bases:

- **Miller's Law** ([Laws of UX](https://lawsofux.com/millers-law/)): human
  short-term memory holds 7±2 items reliably. Lanes consume operator working
  memory (which lane is on what task, what's the latest handoff, what's
  blocked); the 7±2 lower bound (5) is a defensible ceiling.
- **Google multi-agent research**
  ([towards a science of scaling agent systems](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/)):
  3-5 agents optimal; 20+ catastrophic; 39-70% reasoning performance drop at
  scale. Lanes are coordination units, not agents, but the same coordination
  cost curve applies.
- **Personal Kanban WIP limits**
  ([Atlassian Kanban WIP](https://www.atlassian.com/agile/kanban/wip-limits)):
  3-5 max parallel activities is the conventional sweet spot for sustained
  throughput vs context-switch cost.

### Resolution when cap is hit

If you want to open lane #6:

1. **Close an existing lane first.** Use `scripts/lane-close.sh <lane>` to
   flip Status to `closed` + prune `.tmp/lane-<lane>/`.
2. **OR** open a GitHub Issue with the `protocol-violation` label requesting
   a temporary cap raise. The Issue body MUST justify the raise (specific
   work that requires more parallelism, expected duration, plan to return
   below cap). User arbitrates.

### Advisory enforcement

```bash
scripts/lane-cap-check.sh [--lanes-file PATH] [--memory-dir PATH]
                          [--max N] [--format text|json]
```

| Flag | Effect |
|------|--------|
| `--max N` | Override the cap (default 5). |
| `--format text\|json` | Output format. |
| `--lanes-file PATH` | Override LANES.md location. |
| `--memory-dir PATH` | Override memory dir. Defaults to `MERCURY_MEMORY_DIR` env, then `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory`. |
| `MERCURY_MEMORY_DIR` (env) | Same effect as `--memory-dir`. |

Exit `0` if count ≤ max, exit `1` if exceeded, exit `2` on argument or
environment errors. Verdict is reported as text or JSON; the script never
mutates LANES.md.

The script is **advisory** — running it before opening a new lane is a
discipline, not an automated gate. Pre-commit hook enforcement was considered
and rejected: side lanes cannot easily install or modify shared hooks, and
the cap is a sociotechnical limit (operator + maintainer review) rather than
a mechanical one.

### Sample text output

```
lane-cap-check: 2 active lane(s), cap=5 → within_cap
  active: main,side-multi-lane
```

When exceeded:

```
lane-cap-check: 6 active lane(s), cap=5 → exceeded
  active: main,side-mlane,side-foo,side-bar,side-baz,side-qux
  resolution: close an existing lane OR open Issue with `protocol-violation` label requesting cap raise (per feedback_lane_protocol.md HARD-CAP §)
```

### `protocol-violation` GitHub label

Defined in the Mercury repo at #314 implementation time. Color `#B60205`
(GitHub red), description: "Multi-lane protocol violation (e.g. >5 active
lanes, Rule X breach) requiring user arbitration".

Operators opening cap-raise Issues use this label so the user can triage all
protocol violations from one filter.

## Tests

```bash
scripts/test-lane-cap-check.sh
```

32 cases covering arg validation, within-cap, boundary (count == max),
exceeded, custom max, closed-lane exclusion, JSON output validity (including
quote/backslash hostile lane names), parser robustness (orphan-no-status
WARN, zombie-in-Closed-section exclusion), and empty Active Lanes section.
Tests do NOT touch real GitHub or LANES.md — synthetic fixtures only.

## Source references

- Issue [#313](https://github.com/392fyc/Mercury/issues/313) — Δ6 acceptance criteria
- Issue [#314](https://github.com/392fyc/Mercury/issues/314) — Δ7 acceptance criteria
- [v0.1 Delta companion §Δ6](../lane-protocol-v0.1-deltas.md#delta-6--rule-2-shorter-branch-prefix-p3)
- [v0.1 Delta companion §Δ7](../lane-protocol-v0.1-deltas.md#delta-7--hard-cap-at-5-active-lanes-doc-only)
- [Limiting Git Branch Names to 28 Characters (LeanTaaS)](https://medium.com/leantaas-engineering/why-are-we-limiting-git-branch-name-length-to-28-characters-c49cb5f4ff9a)
- [Best practices for naming Git branches (Graphite)](https://graphite.com/guides/git-branch-naming-conventions)
- [Miller's Law (Laws of UX)](https://lawsofux.com/millers-law/)
- [Towards a science of scaling agent systems (Google research)](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/)
- [Working with WIP limits for Kanban (Atlassian)](https://www.atlassian.com/agile/kanban/wip-limits)
