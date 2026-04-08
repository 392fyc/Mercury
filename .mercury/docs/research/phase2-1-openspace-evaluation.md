# Phase 2-1 ADR — `HKUDS/OpenSpace` Evaluation

**Status**: REJECT (for Phase 2 Quality Gate purpose)
**Date**: 2026-04-08
**Issue**: #203
**Parent**: Phase 2 #181
**Decision authority**: Mercury main agent + user confirmation
**Research method**: 4-round `autoresearch` skill invocation from Main session (5+ unique source domains, mechanical quality gate passed). All load-bearing findings are reproduced inline in Sections 1–7 with permalinked vendor sources — this ADR is the canonical, self-contained audit artifact.
**Upstream pin**: All `HKUDS/OpenSpace` source URLs in this ADR are permalinked to commit `4791133e11a45872b063c75968965c447e835455` (2026-04-07T12:44:53Z, latest on `main` at the time of evaluation, commit message *"update readme"*). Dynamic data points from PyPI (`https://pypi.org/pypi/openspace/json`) and the GitHub API (`/repos/HKUDS/OpenSpace`) are **captured verbatim inline** in Sections 3 and 6 with the 2026-04-08 capture timestamp, so audit reproduction does not depend on the live URLs. The single live PyPI URL in the References list is a convenience pointer — the name-collision claim itself is witnessed by the inline JSON snapshots in Section 3. If post-merge audit needs to re-verify the live state, use the Wayback Machine snapshot pattern `https://web.archive.org/web/20260408*/[URL]`.

---

## Context

Phase 2 of Mercury's execution plan (`.mercury/docs/EXECUTION-PLAN.md` §2-3 *"Stop Hook 实现"*) requires a Quality Gate hook that **blocks dev sub-agents from stopping while tests are failing**:

> Dev sub-agent 不能在 test 未通过时 stop

GSD (Issue #191, PR #193) was **REJECTED** — no Stop/SubagentStop hook at all.
OMC (Issue #194, PR #195) was **DEFERRED** — Stop hook exists but the gate is LLM-level rather than mechanical exit-code.
Superpowers (Issue #196, PR #197) was **REJECTED** — strictly weaker than OMC, no Stop hook scaffold to interpose on.

`HKUDS/OpenSpace` is the **fourth and final** candidate from `DIRECTION.md`. DIRECTION.md described it as:
> *"self-evolving skill engine for AI agents (3,900+ Stars), supports Claude Code as a target runtime"*

There is also an existing parallel research issue (#141) tracking OpenSpace's self-evolving skill engine for Mercury's *agent experience accumulation* workstream. **This ADR addresses only the Phase 2 Quality Gate question, not the broader #141 scope.** The Phase 2 verdict does NOT close #141 — the recycled findings have been posted to #141 as a comment for future harness self-check work.

Per Mercury's mount-first principle (`CLAUDE.md`): *"If an external project can solve the problem, mount it via submodule rather than reimplementing."*

---

## Decision

**REJECT** `HKUDS/OpenSpace` as a Phase 2 Quality Gate mount.

Four independent reasons, any one sufficient on its own:

1. **Zero Claude Code hook infrastructure** — no `hooks.json`, no `.claude/` directory, no `Stop`/`SubagentStop` hook files. GitHub code search returns 0 hits for `SubagentStop` repository-wide.
2. **Category mismatch, structurally identical to Superpowers' REJECT** — OpenSpace is a self-evolving skill framework + MCP server. Its "quality gates" terminology refers to skill evolution pipeline safeguards (anti-loop guards, confirmation gates, prompt-injection detection), NOT test-run gating.
3. **Adapter-LOC math fails on the dimension that matters** — the only viable mount mode (MCP integration, ~30 LOC adapter) doesn't touch Claude Code Stop events at all. A submodule + bridge-adapter alternative would require Mercury to write the test-run-and-check-exit-code primitive from scratch because OpenSpace has no such primitive to wrap. That is the self-research outcome the mount-first principle forbids.
4. **Maturity red flag, unique among all 4 candidates** — repo is 15 days old at evaluation time, pre-1.0 (`v0.1.0`), with 8 runtime bugs patched in a single review round 3 days ago, an MCP security leak (`fix(security): stop leaking Python tracebacks to MCP clients`) patched 5 days ago, and the SSE/HTTP transport layer added 1 day before evaluation.

This is REJECT rather than DEFER because, unlike OMC (which has a Stop hook scaffold that could in principle be patched with an exit-code interposer), OpenSpace has nothing to interpose on AND its API surface is too immature to commit to.

---

## Rationale

### 1. NO Claude Code hook infrastructure exists in OpenSpace

Full recursive file listing for `HKUDS/OpenSpace` at the pinned commit (via `gh api repos/HKUDS/OpenSpace/git/trees/main?recursive=1`) contains:

- **NO** `hooks.json` at any path
- **NO** `.claude/` directory at any path
- **NO** `claude-plugin.json` or `.claude/agents/` structure
- **NO** `Stop`, `SubagentStop`, `PreToolUse`, or `PostToolUse` hook files
- The ONLY file with "hook" in its name is `frontend/src/hooks/useSkillEvolutionGraphData.ts` — this is a **React state hook** (TypeScript UI hook) used by the local web dashboard, not a Claude Code hook.

GitHub code search API cross-check (captured 2026-04-08T09:05Z, verbatim):

```bash
$ gh api 'search/code?q=repo:HKUDS/OpenSpace+%22SubagentStop%22'
{"total_count": 0, "items": []}
```

Combined with the recursive tree listing showing zero hook files, this is decisive: **OpenSpace does not contain a Stop or SubagentStop hook at any layer — mechanical or LLM-level**. There is no scaffold to interpose on.

Root directory structure at `4791133` (verbatim from `gh api repos/HKUDS/OpenSpace/contents`):

```
.gitignore
COMMUNICATION.md
LICENSE
README.md
README_CN.md
assets/          (images)
frontend/        (React dashboard UI)
gdpval_bench/    (GDPval benchmark + auto-generated skill library)
openspace/       (Python package source)
pyproject.toml
requirements.txt
showcase/        (demo assets)
```

Sources:
- https://github.com/HKUDS/OpenSpace/tree/4791133e11a45872b063c75968965c447e835455 (tree view)
- GitHub Code Search API response captured inline above 2026-04-08T09:05Z

### 2. Self-evolving skill framework + MCP server, NOT a test gate

OpenSpace is delivered as a **Python 3.12+ package** (`openspace`) with **5 CLI entry points**, declared in [`pyproject.toml`](https://raw.githubusercontent.com/HKUDS/OpenSpace/4791133e11a45872b063c75968965c447e835455/pyproject.toml) at the pinned commit:

| Entry point | Purpose |
|---|---|
| `openspace` | Main interactive CLI (REPL with `>>>` prompt + `--query` mode) |
| `openspace-server` | Server mode (likely the local dashboard backend) |
| `openspace-mcp` | MCP server: `openspace.mcp_server:run_mcp_server` — this is the Claude Code integration path |
| `openspace-download-skill` | Cloud skill registry client (download from open-space.cloud) |
| `openspace-upload-skill` | Cloud skill registry client (upload to open-space.cloud) |
| `openspace-dashboard` | Local web dashboard launcher |

Core runtime dependencies (verbatim from `pyproject.toml`):
- `litellm>=1.70.0,<1.82.7` (with explicit supply-chain-compromise pin note)
- `anthropic>=0.71.0`
- `openai>=1.0.0`
- `mcp>=1.0.0`

Source layout at `4791133`:

```
openspace/
├── __init__.py
├── __main__.py            (CLI entry — refresh-cache, --query, interactive REPL)
├── agents/                (agent definitions: base.py, grounding_agent.py)
├── cloud/                 (cloud community skill sharing: auth, client, embedding, search)
│   └── cli/               (upload_skill.py, download_skill.py)
├── config/                (config loader for agents, grounding, mcp, security)
├── dashboard_server.py    (local web dashboard backend)
└── grounding/backends/
    ├── gui/               (GUI grounding via anthropic computer use)
    └── mcp/               (MCP grounding backend)
```

This is a **self-contained agent framework** with its own agent loop, cloud backend, and grounding mechanisms — NOT a Claude Code plugin in any shape. The framework's value proposition is:

- Self-evolving skill library (auto-fix, auto-improve, auto-learn) with cloud-community sharing
- Token efficiency via skill reuse instead of re-reasoning from scratch
- GUI + MCP grounding for agent task execution

OpenSpace's "quality gates" terminology in its [README at `4791133`](https://raw.githubusercontent.com/HKUDS/OpenSpace/4791133e11a45872b063c75968965c447e835455/README.md) refers to:

> *"Confirmation gates reduce false-positive triggers...Anti-loop guards prevent runaway evolution cycles...Safety checks flag dangerous patterns (prompt injection, credential exfiltration)...Evolved skills are validated before replacing predecessors"*

And the quality monitoring dimensions:

> *"🎯 Skills — applied rate, completion rate, effective rate, fallback rate...🔨 Tool Calls — success rate, latency, flagged issues...⚡ Code Execution — execution status, error patterns"*

These gate the **skill evolution pipeline itself** (preventing runaway self-modification of the skill library), NOT test execution. There is no primitive for *"if the last test command exited non-zero, block the Stop event"*. Mercury's Phase 2 acceptance criterion is **orthogonal** to the mechanism OpenSpace provides.

This is the **same semantic gap as Superpowers' REJECT** — both projects are skill-library-class tools mistakenly considered for a test-gate-class job. Like Superpowers, there is no bridge path analogous to OMC's `decision: "block"` Stop scaffold, because there is no Stop scaffold to repurpose.

Cross-check from `openspace/__main__.py` at the pinned commit ([source](https://raw.githubusercontent.com/HKUDS/OpenSpace/4791133e11a45872b063c75968965c447e835455/openspace/__main__.py)): defines `refresh-cache` subcommand, single-query mode (`--query`/`-q`), and an interactive REPL with `status`/`help`/`exit` subcommands. Has `KeyboardInterrupt` handling and async `cleanup()` lifecycle. Has **no test-runner invocation, no exit-code gating, no event emission, no hook dispatch primitive**. The only "hooks" in this file are `UIIntegration.attach_llm_client()` / `attach_grounding_client()` — internal dependency-injection setters for the live-display UI, completely unrelated to Claude Code hooks.

### 3. Integration model: MCP server + host skill copy (NOT submodule mountable as Claude Code plugin)

Per the README at `4791133`, the documented Claude Code integration ("Path A") uses the Model Context Protocol — verbatim configuration snippet the README shows users:

```json
{
  "mcpServers": {
    "openspace": {
      "command": "openspace-mcp",
      "toolTimeout": 600,
      "env": {
        "OPENSPACE_HOST_SKILL_DIRS": "/path/to/your/agent/skills",
        "OPENSPACE_WORKSPACE": "/path/to/OpenSpace",
        "OPENSPACE_API_KEY": "sk-xxx (optional, for cloud)"
      }
    }
  }
}
```

And two host skills are copied into the consumer's skills directory (verbatim README quote):

> *"Copy skills into your agent's skills directory: `cp -r OpenSpace/openspace/host_skills/delegate-task/ /path/to/your/agent/skills/` and `cp -r OpenSpace/openspace/host_skills/skill-discovery/ /path/to/your/agent/skills/`. Done. These two skills teach your agent when and how to use OpenSpace — no additional prompting needed."*

Verified via Contents API (`gh api repos/HKUDS/OpenSpace/contents/openspace/host_skills?ref=4791133`):

```json
[
  {"name": "README.md", "type": "file"},
  {"name": "delegate-task", "type": "dir"},
  {"name": "skill-discovery", "type": "dir"}
]
```

So the skill-copy integration path described in the README is real and consists of exactly 2 skill directories.

#### Adapter LOC scenarios

| Scenario | Description | Mercury write scope | LOC | Phase 2 outcome |
|---|---|---|---|---|
| A | MCP mount: `pip install -e modules/openspace` + add `openspace-mcp` to `.claude/mcp.json` + copy 2 host skills into `.claude/skills/` | `.claude/mcp.json` ~10 LOC; settings tweak ~5 LOC; host skill copy scripted ~15 LOC | **~30 LOC** | Doesn't touch Stop events at all |
| B | Submodule + custom Stop-hook adapter that intercepts sub-agent completion and calls OpenSpace's skill-quality API | Mercury writes a NEW Stop hook from scratch that runs the test command, checks exit code, and returns `decision: "block"` on failure — OpenSpace contributes nothing here because it has no test-run primitive | **~100-200 LOC of self-research** | Violates mount-first principle |
| C | None — REJECT for Phase 2 Quality Gate purpose | 0 | 0 | Phase 2-1 escalation (see Consequences) |

Mercury's <200 LOC cap is technically satisfiable by scenario B, but the underlying semantic problem is that **OpenSpace does not expose any "run the test command and check its exit code" primitive**. The adapter would be implementing the test-gate logic itself with OpenSpace as a thin context provider — exactly the self-research outcome the mount-first principle forbids. The LOC math is irrelevant when the underlying mechanism does not exist in the dependency.

#### PyPI publication: NAME-SQUATTED, no usable PyPI install path

Live PyPI lookup of `https://pypi.org/pypi/openspace/json` (captured 2026-04-08T09:15Z) returns:

| Field | Value |
|---|---|
| Package name | `openspace` |
| Latest version | `2.5.1` |
| Maintainer | Brandon Sexton (`brandon.sexton.1@outlook.com`) |
| Upload date | 2023-01-29 |
| Description | *"astrodynamics analysis and simulation package...a public astrodynamics analysis and simulation sandbox not intended for commercial use"* |

This is an **unrelated 2023 astrodynamics package** by a different maintainer. HKUDS/OpenSpace declares `name = "openspace"` version `0.1.0` in its `pyproject.toml` at the pinned commit, but this name is already taken on PyPI by Brandon Sexton's package.

**Mutually-witnessing data points** (frozen inline, audit-reproducible without re-fetching):

| Source | Declared name | Latest version | Maintainer |
|---|---|---|---|
| `pyproject.toml` at `4791133` in HKUDS/OpenSpace | `openspace` | `0.1.0` | HKUDS team |
| `https://pypi.org/pypi/openspace/json` 2026-04-08T09:15Z | `openspace` | `2.5.1` | Brandon Sexton |

**Consequence**: Mercury cannot `pip install openspace` — that installs Brandon Sexton's astrodynamics package, not HKUDS/OpenSpace. The only viable consumption paths are `pip install -e modules/openspace` (after `git submodule add`) or `pip install git+https://github.com/HKUDS/OpenSpace`. This is the same name-squatting situation as `obra/superpowers` vs npm `superpowers` (which is squatted by `01studio`).

### 4. Architecture is 15 days old, pre-1.0, with active churn

Verbatim from `gh api repos/HKUDS/OpenSpace` (captured 2026-04-08T09:05Z):

```json
{
  "archived": false,
  "created_at": "2026-03-24T08:01:49Z",
  "default_branch": "main",
  "forks": 547,
  "language": "Python",
  "license": "MIT",
  "open_issues": 32,
  "pushed_at": "2026-04-07T12:45:04Z",
  "stars": 4682
}
```

**Critical observation**: the repository was **created 2026-03-24**, meaning it is **only 15 days old** at evaluation time. In those 15 days it has accumulated 4,682 stars and 547 forks — aggressive viral adoption — but:

- **Immature architecture surface**: any project at the 2-week mark is still in heavy churn
- **No tagged release**: `version = "0.1.0"` in pyproject.toml is the canonical pre-release marker
- **No stability track record**: impossible to assess whether the API will be stable enough for Mercury to mount and maintain

Recent 15 commits to `main` (verbatim from `gh api repos/HKUDS/OpenSpace/commits?per_page=15` at 2026-04-08T09:10Z):

| Date | Commit | Message |
|---|---|---|
| 2026-04-07 12:44 | `4791133` | update readme |
| 2026-04-07 12:06 | `114f06b` | **feat: support SSE and streamable HTTP for OpenSpace MCP** |
| 2026-04-06 12:14 | `b0021b4` | Merge pull request #61 from HKUDS/pr-60 |
| 2026-04-06 12:13 | `a23792a` | fix: tighten pr-60 review follow-ups |
| 2026-04-06 11:18 | `a3a7340` | fix: resolve pr-60 review regressions |
| 2026-04-05 17:02 | `f3a064d` | **fix: address 8 runtime bugs found during code review** |
| 2026-04-05 07:13 | `1bd1a3d` | clean up LLM credential resolution |
| 2026-04-03 16:05 | `81f375e` | update readme |
| 2026-04-03 15:51 | `456184f` | Merge pull request #51 from HKUDS/review/xzq-batch-20260403 |
| 2026-04-03 15:44 | `34d82b7` | fix: make tool fallback conservative |
| 2026-04-03 15:10 | `af1eb5b` | **fix(security): stop leaking Python tracebacks to MCP clients** |
| 2026-04-03 15:10 | `f89ea89` | fix: use resolved tool_obj for fallback tool execution |
| 2026-04-03 15:10 | `6f581f6` | fix: add missing Logger.set_level() method |
| 2026-04-03 15:10 | `171c1b7` | fix: replace ErrorCode enum calls with GroundingError raises |
| 2026-04-03 15:09 | `c15229a` | fix(frontend): keep product proper nouns in English for zh locale (#50) |

**Red-flag signals** (all verbatim from commit messages above):

1. **8 runtime bugs in a single code review round** (`f3a064d`, 2026-04-05) — for a 2-week-old project, having 8 bugs surface in one review pass indicates the core paths are still being exercised for the first time by external users. Mercury would be running into bug #9, #10, #11 if it mounted now.
2. **Security fix 5 days ago** (`af1eb5b`, 2026-04-03) — *"stop leaking Python tracebacks to MCP clients"* is a non-trivial MCP server information-disclosure leak. The MCP integration surface (which is the only viable Mercury integration path per Section 3) was leaking stack traces to clients until essentially yesterday in evaluation terms. Mercury must NOT mount an MCP server with such recent information-disclosure history.
3. **Transport layer added 1 day before evaluation** (`114f06b`, 2026-04-07) — adding SSE/HTTP transport to an MCP server is not a cosmetic change. A transport layer with 24 hours of existence has had zero days of stability testing under real load.
4. **Multiple PR review regression follow-ups** (`a23792a`, `a3a7340`) — PRs land with regressions that need follow-up commits within hours. This is a "move fast and break things" cadence, not a "production-ready mounting target" cadence.

This is uniquely bad among the 4 Phase 2-1 candidates. The other three were all multi-month minimum and post-1.0; OpenSpace is alone in the pre-1.0 + active-stability-crisis bracket.

### 5. Windows support: reasonable at the Python layer, but moot for Phase 2

From `pyproject.toml` at the pinned commit, optional Windows dependencies are explicitly declared in `[project.optional-dependencies]`:
- `pywinauto` (for GUI grounding on Windows)
- `pywin32`

The README acknowledges *"stdio deadlock on Windows"* as a resolved issue (no version/commit specified in the excerpt). No tmux dependency, no signal trapping, no POSIX-only primitives in the README or pyproject. The MCP integration path is platform-agnostic — `openspace-mcp` runs as a stdio subprocess, which works on any OS.

**Windows verdict**: reasonable at the package installation layer. Since OpenSpace is not providing a hook (the dimension that matters for Mercury's Phase 2), Windows compatibility of the hook layer is moot — there is no hook layer.

### 6. Adapter LOC scenarios — see Section 3 for the table

Already covered in Section 3 (the LOC scenario table). Key takeaway repeated for clarity: the only LOC-friendly mount mode (scenario A, ~30 LOC) does not touch Stop events. The only Phase-2-relevant mount mode (scenario B, ~100-200 LOC) violates the mount-first principle. **The LOC math is irrelevant when the underlying mechanism does not exist in the dependency.**

### 7. Four-way comparison vs. GSD, OMC, and Superpowers

| Dimension | GSD | OMC | Superpowers | **OpenSpace** |
|---|---|---|---|---|
| Stop/SubagentStop hook | None | `persistent-mode.cjs` Priority 7 | None | **None** (0 code-search hits; no `hooks.json`) |
| Stop gate type | N/A | LLM-level (Ralph loop) | N/A | **N/A** |
| Mercury criterion fit | FAIL | PARTIAL | FAIL | **FAIL** |
| Submodule mountable | Possible/undocumented | Plugin-only | Possible + plugin runtime assumptions | **Python package + MCP server (pip install -e modules/openspace)** |
| Claude Code integration method | Plugin/marketplace | Plugin/marketplace | Plugin/marketplace | **MCP server config + host-skill copy** |
| Anthropic marketplace | No | No | Yes | **No** |
| npm/PyPI publication | `get-shit-done-cc@1.34.2` (npm) | `oh-my-claude-sisyphus` (npm) | Not published (`01studio` squats `superpowers` on npm) | **Not published** (`openspace` on PyPI is Brandon Sexton's 2023 astrodynamics package v2.5.1) |
| Windows native | Bash + issues | Patched + tested | Polyglot wrapper, freeze fixed v3.6+ | **Optional `pywinauto`/`pywin32` deps; stdio deadlock noted resolved**; no tmux |
| License | MIT | MIT | MIT | **MIT** |
| Repo age | ~1 year | ~3-6 months | ~6 months | **15 days** |
| Activeness | Active, 48k stars | Active | Very active (421 commits since Oct 2025, v5.0.7) | **Extreme churn** — 4.7k stars / 15 days, 8 runtime bugs + MCP security leak + transport churn in past week |
| Maturity | Mature | Mature | Mature (v5.0.7) | **Pre-1.0 (`v0.1.0`), transport layer added 1 day before eval** |
| Decision | **REJECT** | **DEFER** | **REJECT** | **REJECT (double failure: category mismatch AND immaturity)** |

---

## Consequences

- **Phase 2-1 candidate sequence is now exhausted.** All 4 DIRECTION.md candidates have been evaluated:
  - GSD → REJECT (PR #193)
  - OMC → DEFER (PR #195)
  - Superpowers → REJECT (PR #197)
  - OpenSpace → REJECT (this ADR)

- **🚨 Phase 2-1 escalation point reached** — per the Superpowers ADR Consequences section: *"if OpenSpace also fails, OMC is the only remaining candidate that ships an actual Stop hook scaffold. However, OMC's Stop gate is LLM-level rather than mechanical exit-code, which does not by itself satisfy EXECUTION-PLAN.md §2-3 'Stop Hook 实现' (dev sub-agent 不能在 test 未通过时 stop requires harness-level enforcement). At that point, the decision must escalate back to the user with two explicit options: (a) relax the Phase 2 acceptance criterion to accept the LLM-level gap, or (b) implement a thin Mercury adapter that interposes a mechanical exit-code check between OMC's Stop hook and the dev sub-agent (a ~50–150 LOC adapter satisfying the <200 LOC cap, which would still honor mount-first because it mounts OMC and only wraps its output)."* **This ADR triggers that escalation but does NOT pre-commit to either option** — that is a user decision after merge.

- **REJECT is not equivalent to "OpenSpace is bad"**. OpenSpace is a novel and valuable project for its actual use case (self-evolving skill library + cloud-community sharing). The REJECT applies **strictly** to the Phase 2 Quality Gate purpose. The features that motivated the original Mercury OpenSpace research (Issue #141 — agent experience accumulation) are intact and remain candidates for future Mercury harness self-check enhancement work. A recycle-comment with the relevant findings has been posted to #141 with deferred-research tracking criteria (wait for v1.0 + 30 days production use + PyPI publication).

- **Mount-first principle preserved**: REJECT is justified by absence of mechanism + immaturity, not preference for self-research. The submodule + bridge-adapter alternative was explicitly considered (scenario B in Section 3) and rejected because it would require Mercury to write the test-gate primitive from scratch — exactly the outcome mount-first forbids.

- **Post-Phase-2 follow-up tracking** (recorded on Issue #141, NOT this Issue #203):
  - [ ] OpenSpace reaches v1.0 (currently `0.1.0`)
  - [ ] OpenSpace publishes to PyPI under a non-collided name
  - [ ] 30+ days since the most recent security or transport churn
  - [ ] Re-evaluate `delegate-task` and `skill-discovery` host skills against Mercury's current `.claude/skills/` set
  - [ ] Re-evaluate `openspace-mcp` quality-monitoring API as input source for Mercury's harness self-check metrics

---

## Verification

**Evidence pattern**: This ADR is the canonical, self-contained audit artifact. All load-bearing findings (verbatim hook-absence finding via tree listing + code search, MCP config snippet from README, commit list, comparison table, PyPI name-collision JSON snapshot, pyproject metadata) are reproduced **inline** in Sections 1–7 above with vendor-source permalinks pinned to commit `4791133`. The code-search and GitHub Contents API responses are captured **verbatim inline** in Sections 1, 3, and 4 (not behind a reachable public URL — `https://github.com/search?q=repo:HKUDS/OpenSpace+SubagentStop&type=code` requires GitHub authentication and is not browseable anonymously, so the inline JSON snapshot IS the audit record for that claim, not the URL). A reviewer can independently re-verify every load-bearing claim by either clicking the `raw.githubusercontent.com` permalinks (for file-content claims) or by re-running `gh api` against the same endpoints (for API-response claims). No Mercury-local file is required for verification. The autoresearch process generated a supplementary scratch report at `.research/reports/RESEARCH-openspace-evaluation-2026-04-08.md` in the operating session; per Mercury convention (also applied to the merged GSD ADR PR #193, OMC ADR PR #195, and Superpowers ADR PR #197), that scratch file is not committed because the ADR Rationale sections fully reproduce the load-bearing content.

Final autoresearch gate metrics:

| Metric | Value | Threshold | Status |
|---|---|---|---|
| `question_answer_rate` | 7/7 = 1.00 | ≥ 0.9 | PASS |
| `citation_density` | ~0.94 | ≥ 0.75 | PASS |
| `unverified_rate` | ~0.01 (1 PyPI-state UNVERIFIED note resolved in Round 4 via direct JSON API query) | ≤ 0.1 | PASS |
| `iteration_depth` | 4 | ≥ 4 | PASS |
| `source_diversity` | 5+ unique domains (github.com, raw.githubusercontent.com, api.github.com, pypi.org, anthropic.com docs) | ≥ 4 | PASS |

Verification: PASS — mechanical checklist completed in the operating session. Per the *Evidence pattern* note above, the load-bearing reproduction lives **in this ADR's Sections 1–7** (with permalinked vendor sources), not in the local-scratch transcript file.

---

## References

- Phase 2 parent Issue: #181
- This evaluation Issue: #203
- Parallel research issue (recycled findings posted as comment): #141
- GSD evaluation (REJECTED): `.mercury/docs/research/phase2-1-get-shit-done-evaluation.md`, PR #193
- OMC evaluation (DEFERRED): `.mercury/docs/research/phase2-1-omc-evaluation.md`, PR #195
- Superpowers evaluation (REJECTED): `.mercury/docs/research/phase2-1-superpowers-evaluation.md`, PR #197
- Mercury direction: `.mercury/docs/DIRECTION.md`
- Phase 2 acceptance criterion: `.mercury/docs/EXECUTION-PLAN.md` §2-3 *"Stop Hook 实现"*
- Mount-first principle: `CLAUDE.md` MUST section
- OpenSpace repo: https://github.com/HKUDS/OpenSpace
- OpenSpace README at pinned commit: https://raw.githubusercontent.com/HKUDS/OpenSpace/4791133e11a45872b063c75968965c447e835455/README.md
- OpenSpace `pyproject.toml` at pinned commit: https://raw.githubusercontent.com/HKUDS/OpenSpace/4791133e11a45872b063c75968965c447e835455/pyproject.toml
- OpenSpace `__main__.py` at pinned commit: https://raw.githubusercontent.com/HKUDS/OpenSpace/4791133e11a45872b063c75968965c447e835455/openspace/__main__.py
- PyPI name collision (live URL, snapshot in Section 3): https://pypi.org/pypi/openspace/json
- Stop hook docs: https://code.claude.com/docs/en/hooks
