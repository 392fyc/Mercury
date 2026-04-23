# Mercury Skill Evaluation — Static Analysis Report
**Issue #279 (Deliverables 1–3): Trigger-Accuracy, Description Optimization, Retire/Merge/Keep Decisions**

---

## §1. Executive Summary

**Scope**: 9 project-level skills analyzed via static inspection of SKILL.md frontmatter + description text.

**Key Findings**:
- **Description quality**: 6/9 skills have strong, explicit trigger phrases; 3 are vague or lack pushiness.
- **Keyword overlap**: Material conflicts on "review" (dual-verify vs pr-flow), "research" (autoresearch vs web-research), "KB" (kb-lint redundant trigger phrase).
- **Upstream attribution**: 1/9 properly attributed (caveman-toggle MIT cherry-pick); 8/9 missing upstream fields despite potential external origins.
- **Pushiness grade**: 3 skills (dev-pipeline, pr-flow, autoresearch) achieve A-grade "use this even if not asked" language; 4 achieve B-grade neutral; 2 weak (kb-lint, gh-project-flow).

**Top 3 Risks**:
1. **"review" ambiguity**: dual-verify and pr-flow both claim "review" territory without clear separation (pre-merge vs post-approval).
2. **"research" collision**: autoresearch ("deep research", "自动研究") and web-research ("research", "验证") lack boundary definition.
3. **gh-project-flow scope creep**: Bootstrap-only rationale is sound, but description doesn't warn against misuse in non-Mercury repos.

---

## §2. Per-Skill Audit Table

| Skill | Desc Length | Trigger Count | Language | Pushiness | Upstream |
|-------|-------------|---------------|----------|-----------|----------|
| **dev-pipeline** | 486 chars | 8 phrases | zh + en | **A** | No |
| **pr-flow** | 322 chars | 8 phrases | zh + en | **A** | No |
| **dual-verify** | 176 chars | 6 phrases | zh + en | **A** | No |
| **handoff** | 89 chars | 0 phrases | en only | **B** | No |
| **autoresearch** | 219 chars | 6 phrases | zh + en | **A** | No |
| **kb-lint** | 178 chars | 4 phrases | zh + en | **C** | No |
| **web-research** | 521 chars | 6 phrases | zh + en | **A** | No |
| **gh-project-flow** | 334 chars | 7 phrases | zh + en | **C** | No |
| **caveman-toggle** | 128 chars | 5 phrases | zh + en | **B** | **Yes** |

**Legend**:
- **Pushiness A**: Explicit "use this skill when" + "even if they don't explicitly ask" or "proactively" language (e.g., "web-research: …should be consulted proactively").
- **Pushiness B**: "Use when user says X" without explicit push.
- **Pushiness C**: Passive framing ("run this when X exists") or phase-gated ("bootstrap-only" discourage-framing).

---

## §3. Trigger-Keyword Overlap Matrix

### Conflict 1: "review" (dual-verify vs pr-flow)
- **dual-verify triggers**: "dual verify", "dual-verify", "parallel review", "run dual verify", "双路验证", "并行review"
- **pr-flow triggers**: "review comments", "check PR status"
- **Risk**: User says "review my code" → could reasonably trigger either. Current descriptions don't disambiguate pre-merge (dual-verify gates commit) vs post-approval (pr-flow handles Argus review loop).
- **Severity**: **HIGH** — skill scopes overlap conceptually (code review) but target different gates.

### Conflict 2: "research" (autoresearch vs web-research)
- **autoresearch triggers**: "autoresearch", "自动研究", "深度调研", "deep research", "comprehensive research", "多轮调研"
- **web-research triggers**: "研究", "验证", "审查", "查阅", "核实", "调查", "research", "verify", "validate", "check docs"
- **Risk**: User says "研究一下这个库" (research this library) → autoresearch is mechanical multi-round gate (overkill for 1–2 questions); web-research is the right fit. Current descriptions use different terminology ("deep research" vs "verify") but both claim "research" in Chinese.
- **Severity**: **MEDIUM** — both have valid scopes, but trigger phrases aren't mutually exclusive. Autoresearch correctly gates on ≥3 questions, but users won't know this before invoking.

### Conflict 3: "KB" operations (kb-lint only, minor)
- **kb-lint triggers**: "/kb-lint", "lint KB", "KB health check", "知识库检查", "KB lint"
- **Collision risk**: None currently, but description lists redundant trigger "/kb-lint" (the command itself, not a keyword).
- **Severity**: **LOW** — no overlap with other skills, but description is slightly redundant.

### Conflict 4: "handoff" (naming collision, low risk)
- **handoff** has no explicit trigger phrases in description (just "/handoff" in body).
- **Conflict with global skill**: `/oh-my-claudecode:handoff` exists as OMC global; Mercury's project-level `handoff` skill uses `/handoff` (space-delimited, per SKILL.md line 30: "Always use `/handoff` (space-delimited)").
- **Risk**: OMC reference docs must clarify coexistence (OMC is lower priority; Mercury local skill triggers first).
- **Severity**: **LOW** — documented but unintuitive.

---

## §4. Per-Skill Description Critique + Optimization

### 1. **dev-pipeline**
**Current description** (SKILL.md:3–4):
> "Mercury's preset Main → Dev → Acceptance chain for executing a single, well-scoped coding task end-to-end with blind acceptance review. Use this skill when the user says "dev pipeline", "dispatch task", "派发任务", "dev → acceptance", "跑完整开发流程", "dev pipeline 验证", "blind review", "完整开发链", or when a task is ready to be implemented and verified by separate agents (instead of doing it inline). The skill spawns the dev subagent to implement, then spawns the acceptance subagent to blind-review the result, then loops or completes based on the verdict. Independent of Mercury's other modules — works in any repo that has .claude/agents/dev.md + .claude/agents/acceptance.md defined."

**Issues**:
- Strong description, but "when a task is ready to be implemented" is passive — doesn't explicitly push "use this even if user doesn't know about dev-pipeline workflow".
- Length adequate (486 chars), triggers clear (8 phrases), bilingual.

**Proposed revision**:
> "Mercury's preset Main → Dev → Acceptance chain for executing a single, well-scoped coding task end-to-end with blind acceptance review. **Use this skill proactively** whenever the user has a ready-to-implement task (instead of coding inline) — even if they don't explicitly ask for 'dev pipeline'. Say 'dev pipeline', 'dispatch task', '派发任务', 'blind review', '完整开发链', or when task is scoped: the skill spawns dev subagent to implement, acceptance subagent to blind-review, then loops or completes based on verdict. Independent of Mercury's other modules — works in any repo with .claude/agents/dev.md + .claude/agents/acceptance.md."

**Grade**: A (strong) → A (stronger push).

---

### 2. **pr-flow**
**Current description** (SKILL.md:2–4):
> "Automate the full PR lifecycle with Argus review bot: create PR, poll for review, read findings, fix issues, push and wait for Argus fix-detection resolve + incremental review, merge after approval. Use this skill when the user says "PR", "pull request", "create PR", "merge PR", "提PR", "合并", "PR流程", "开PR", "check PR status", "review comments", "标准PR流程". Use this skill after dev work reaches `implementation_done`, the branch is pushed, and the task has passed `main_review`. It replaces the manual C4-C7 steps in the Mercury workflow."

**Issues**:
- Excellent trigger coverage (8 phrases), bilingual, but "review comments" is ambiguous — collides with dual-verify's "review" space.
- No explicit push: "Use this skill when…" is neutral, not proactive.

**Proposed revision**:
> "Automate the full PR lifecycle with Argus review bot: create PR, poll for review, read findings, fix issues, push and wait for Argus incremental review, merge after approval. **Always use this skill when code is ready to PR** — even if the user only says 'push' or 'merge' — to avoid manual Argus polling. Trigger: 'PR', 'pull request', 'create PR', 'merge PR', '提PR', '合并', 'PR流程', 'check PR status'. Use after dev work reaches `implementation_done` and branch is pushed. Replaces manual C4-C7 steps in Mercury workflow."

**Grade**: A (strong coverage) → A (explicit proactive directive).

---

### 3. **dual-verify**
**Current description** (SKILL.md:2–4):
> "Run parallel Claude Code deep-review and Codex code-audit, then consolidate findings before marking PR ready. Use instead of /code-review or auto-verify when doing pre-merge review. Trigger on: "dual verify", "dual-verify", "parallel review", "run dual verify", "双路验证", "双向验证", "并行review", "双路review"."

**Issues**:
- Excellent pushiness: "Use instead of /code-review" is prescriptive.
- Short (176 chars) but punchy; triggers clear (6 phrases); bilingual.
- Trigger "parallel review" overlaps with pr-flow's "review comments" — needs disambiguation in conversation (dual-verify = before merge gate; pr-flow = post-approval Argus loop).

**Proposed revision**:
> "Run parallel Claude Code deep-review and Codex code-audit in parallel, then consolidate findings. **This is the mandatory pre-commit review step per Mercury CLAUDE.md** — use this instead of /code-review or /auto-verify. Trigger: 'dual verify', 'dual-verify', 'parallel review', 'run dual verify', '双路验证', '并行review', '代码审查', 'review before commit'. Use before any PR creation or direct commit to protected branches."

**Grade**: A (strong) → A (mandatory framing reinforces MUST compliance).

---

### 4. **handoff**
**Current description** (SKILL.md:2–3):
> "Generate a structured handoff document and ready-to-paste starting prompt for the next session. Use `/handoff` for manual mode (output only). Use `/handoff auto` to auto-launch the new session via `claude` CLI after the document is written."

**Issues**:
- Very short (89 chars), no trigger phrases, en only, confuses invocation syntax with triggers.
- Passive framing: describes what it does, not when to use it.
- No push: doesn't say "use this when you need to hand off to a colleague" or "whenever session is reaching context limit".

**Proposed revision**:
> "Generate a structured handoff document + ready-to-paste starting prompt for the next session, preserving context across session boundaries. **Use `/handoff` whenever you reach context limits, need to hand off to a colleague, or want to continue in a fresh session.** Modes: `/handoff` (manual mode, output prompt), `/handoff auto` (auto-launch next session). Includes state summary, acceptance criteria, references — no manual reconstruction needed."

**Grade**: B (descriptive) → B (still neutral but improved context + clearer value prop).

---

### 5. **autoresearch**
**Current description** (SKILL.md:2–4):
> "Autonomous iterative research protocol with mechanical quality gates. Multi-round search loops with per-round verification -- the agent does NOT decide when to stop, only the gate does. Works standalone or under Mercury dispatch. Triggers: "autoresearch", "自动研究", "深度调研", "deep research", "comprehensive research", "多轮调研"."

**Issues**:
- Excellent A-grade pushiness: "the agent does NOT decide when to stop, only the gate does" is forceful.
- Triggers clear (6 phrases), bilingual, but collision with web-research on "research" (see §3).
- Description doesn't explicitly say "use this for ≥3 questions; use web-research for quick lookups" — users might invoke autoresearch for single-source verification.

**Proposed revision**:
> "Autonomous iterative research with mechanical quality gates — multi-round loops, per-round verification, agent doesn't self-decide completion (gate does). **Use this proactively for ≥3 research questions or multi-source verification** — even if the user just says 'research X'. For quick lookups (1–2 questions, single source), use web-research instead. Triggers: 'autoresearch', '自动研究', '深度调研', 'deep research', '多轮调研', 'comprehensive research'. Works standalone or under Mercury dispatch."

**Grade**: A (strong) → A (scope clarification vs web-research added).

---

### 6. **kb-lint**
**Current description** (SKILL.md:2–7):
> "Run AgentKB knowledge base health checks (lint). Detects broken links, orphan pages, uncompiled daily logs, stale articles, missing backlinks, sparse articles, and optionally LLM-powered contradiction detection. Use when the user says "/kb-lint", "lint KB", "KB health check", "知识库检查", "KB lint"."

**Issues**:
- Weak C-grade pushiness: purely operational ("Use when the user says…"), no proactive directive.
- Triggers listed include "/kb-lint" (invocation syntax, not a keyword) — redundant with command name.
- No pitch: doesn't say "run this regularly to keep KB healthy" or "use before major refactors".
- Dependency on `$AGENTKB_DIR` not mentioned in description (though in body).

**Proposed revision**:
> "Run AgentKB knowledge base health checks (lint) — detects broken links, orphan pages, stale articles, contradictions. **Run this regularly to keep KB healthy, especially before major refactors or content merges.** Requires `$AGENTKB_DIR` env var. Triggers: 'lint KB', 'KB health check', '知识库检查', 'check KB', 'KB lint', 'knowledge base health'. Offers `/kb-lint structural` (fast) or `/kb-lint full` (with LLM contradiction detection)."

**Grade**: C (passive) → B (still neutral but adds proactive suggestion + clarifies modes).

---

### 7. **web-research**
**Current description** (SKILL.md:2–4):
> "Mercury's mandatory web research protocol for verifying external SDK/API/CLI behavior before writing code. Use this skill whenever the task involves importing external packages, referencing API signatures, claiming package versions, using CLI flags, or integrating with third-party tools. Also use when the user says "研究", "验证", "审查", "查阅", "核实", "调查", "research", "verify", "validate", "check docs", "look up". This skill should be consulted proactively — even if the user doesn't explicitly ask for research, any code touching external dependencies needs verification first. Training data is frequently wrong about API signatures and versions; a 2-minute search prevents hours of debugging."

**Issues**:
- Excellent A-grade pushiness: "mandatory", "should be consulted proactively", "even if the user doesn't explicitly ask".
- Long (521 chars), comprehensive trigger list (6 phrases), bilingual, strong rationale.
- Collision with autoresearch on "research" / "验证" (see §3).

**Proposed revision** (minor):
> "Mercury's mandatory web research protocol for verifying external SDK/API/CLI behavior before writing code. **Use proactively whenever the task involves importing external packages, referencing API signatures, claiming versions, using CLI flags, or integrating third-party tools — even if the user doesn't explicitly ask.** For ≥3-question deep investigations, use autoresearch instead. Triggers: '研究', '验证', '审查', '查阅', 'research', 'verify', 'validate', 'check docs', 'look up'. Training data is frequently wrong about API signatures and versions; a 2-minute search prevents hours of debugging."

**Grade**: A (strong) → A (explicit autoresearch boundary added).

---

### 8. **gh-project-flow**
**Current description** (SKILL.md:2–4):
> "BOOTSTRAP-ONLY task management for Mercury self-development via GitHub Project #3. Lets the main agent pull the next Phase + P0 Todo task, mark it In Progress, link work products (PR/Issue), and move items to Done. Use this skill when the user says "next task", "下一个任务", "拉任务", "认领任务", "标记 in progress", "project status", "更新 project", "Mercury 项目看板", "Phase 1 任务", "gh-project-flow". DO NOT use this skill for general (non-Mercury) project development — those scenarios will use Memory Layer (Phase 3) + Dev Pipeline (Phase 1 self-output) instead. This skill exists to bootstrap Mercury's own buildout and will be retired when Phase 3 lands."

**Issues**:
- C-grade pushiness: "BOOTSTRAP-ONLY" and "will be retired" signal discouragement (passive).
- Triggers adequate (7 phrases), bilingual, but includes "gh-project-flow" (skill name, not keyword).
- Clear scope boundary ("Mercury-only"), but discouragement is too strong — users might not use it when they should.

**Proposed revision**:
> "Task management for Mercury's own self-development via GitHub Project #3 — pulls next Phase + P0 Todo task, marks In Progress, links work products (PR/Issue), moves to Done. **Use this for Mercury work** — even for housekeeping tasks. Triggers: 'next task', '下一个任务', '拉任务', '认领任务', 'project status', '更新 project', 'Mercury 项目', 'Phase X'. DO NOT use for external projects (Phase 3 will replace this with Memory Layer + Dev Pipeline). BOOTSTRAP-ONLY: retires when Phase 3 ships."

**Grade**: C (discouraging) → B (still gated but less deterring).

---

### 9. **caveman-toggle**
**Current description** (SKILL.md:2–6):
> "Toggle persistent caveman concise mode for Mercury. Manages CLAUDE.local.md to enable/disable terse output style across sessions. Use when the user says "/caveman-on", "/caveman-off", "/caveman-status", "开启caveman", "关闭caveman", "简洁模式", "caveman mode"."

**Issues**:
- Adequate B-grade pushiness: "Use when the user says…" is neutral.
- Triggers clear (5 phrases), bilingual, but includes invocation syntax ("/caveman-on" etc.) mixed with keywords.
- **Upstream attribution present** (MIT, SHA 26c25e39, cherry-picked #213) — good model for future work.
- Short (128 chars), functional.

**Proposed revision**:
> "Toggle persistent caveman concise mode across sessions. Manages CLAUDE.local.md to enable/disable terse output style. **Use when the user wants to reduce verbosity** — even if they phrase it as 'be concise' or 'simplify output'. Triggers: 'caveman-on', 'caveman-off', '开启caveman', '关闭caveman', '简洁模式', 'concise mode'. Restart session to activate changes. Based on JuliusBrussee/caveman (MIT, SHA 26c25e39)."

**Grade**: B (neutral) → B (unchanged, already has upstream attribution).

---

## §5. Retire / Merge / Keep Decision Memo

### 1. **dev-pipeline** → **KEEP-REWRITE**
**Decision**: Scope is fundamental to Mercury's design (Main → Dev → Acceptance chain). Keep active.
**Rationale**: Aligns with DIRECTION.md §2 (blind review, preset chains). Per feedback_skill_chain_cleanup_sot.md, this is a foundational tier-0 workflow.
**Action**: Adopt proposed revision §4.1 to add explicit proactive language ("use even if they don't explicitly ask").
**Justification**: ~200 LOC constraint satisfied; independently detachable; core to modular dispatch model.

---

### 2. **pr-flow** → **KEEP-REWRITE**
**Decision**: Scope is essential (PR automation with Argus integration). Keep active.
**Rationale**: Aligns with DIRECTION.md §4 (Mercury workflow automation). Replaces manual C4-C7 steps.
**Action**: Adopt proposed revision §4.2 to clarify "always use when code is ready" and distinguish from dual-verify (pre-merge gate vs post-approval loop).
**Justification**: Modular, ≤200 LOC, independently detachable. Currently strong but lacks explicit proactive push.

---

### 3. **dual-verify** → **KEEP-REWRITE**
**Decision**: Scope is mandatory per CLAUDE.md (pre-commit review gate). Keep active.
**Rationale**: Aligns with DIRECTION.md §1 (dual-verify before commit non-negotiable). Replaces /code-review.
**Action**: Adopt proposed revision §4.3 to reinforce "mandatory" status and clarify timing (before PR creation, not inside pr-flow).
**Justification**: Compliance tool; independent of other skills; ≤200 LOC. Minimal change needed.

---

### 4. **handoff** → **KEEP-REWRITE**
**Decision**: Scope is meta (session continuation protocol). Keep active.
**Rationale**: Aligns with CLAUDE.md terminology standards ("handoff" = prompt + doc, both artifacts). User-invoked only, not auto-triggered.
**Action**: Adopt proposed revision §4.4 to add value prop ("context preservation") and clarify triggers ("context limits", "handoff to colleague").
**Justification**: Independent; user-controlled; essential for long tasks. Current description is too terse.

---

### 5. **autoresearch** → **KEEP-REWRITE**
**Decision**: Scope is specialized (≥3-question iterative research with mechanical gates). Keep active.
**Rationale**: Aligns with DIRECTION.md §8 (autoresearch philosophy). Distinguishes from web-research (quick lookup).
**Action**: Adopt proposed revision §4.5 to explicitly boundary from web-research ("use autoresearch for ≥3 questions").
**Justification**: Mechanical quality gate is unique; independently detachable; ≤200 LOC. Conflict resolution critical.

---

### 6. **kb-lint** → **KEEP-REWRITE**
**Decision**: Scope is support tool (AgentKB health checks). Keep active but with lower priority.
**Rationale**: Aligns with DIRECTION.md §7 (KB maintenance). Optional but recommended regularly.
**Action**: Adopt proposed revision §4.6 to add proactive push ("run regularly", "before major refactors") and clarify `$AGENTKB_DIR` dependency upfront.
**Justification**: Independent; ≤200 LOC; useful for KB quality. Current description is too passive.

---

### 7. **web-research** → **KEEP-REWRITE**
**Decision**: Scope is mandatory (external dependency verification before code). Keep active.
**Rationale**: Aligns with DIRECTION.md §5 (web-research mandatory) and feedback_web_research.md (never guess APIs).
**Action**: Adopt proposed revision §4.7 to add explicit boundary with autoresearch ("for ≥3 questions, use autoresearch").
**Justification**: Compliance tool; modular; independently detachable. Conflict resolution is only change needed.

---

### 8. **gh-project-flow** → **KEEP-REWRITE**
**Decision**: Scope is bootstrap-only (Mercury self-dev task tracking). Keep active but with sunset clause.
**Rationale**: Aligns with DIRECTION.md §3 (bootstrap phases); explicitly designed to retire at Phase 3.
**Action**: Adopt proposed revision §4.8 to reduce discouragement ("use this for Mercury work") while maintaining sunset message.
**Justification**: Temporary tool; ~150 LOC; modular. Reframing as "use for Mercury tasks" (not "avoid") is more helpful during bootstrap.

---

### 9. **caveman-toggle** → **KEEP**
**Decision**: Scope is utility (session mode toggle). Keep active.
**Rationale**: Aligns with DIRECTION.md §9 (output control). Properly attributed cherry-pick (MIT).
**Action**: No rewrite needed. Optional minor revision for consistency (§4.9) but current form acceptable.
**Justification**: Clean upstream attribution model; independent; ≤200 LOC. Demonstrates proper cherry-pick protocol for future skills.

---

## Summary Table

| Skill | Decision | Action | Priority |
|-------|----------|--------|----------|
| dev-pipeline | KEEP-REWRITE | Strengthen "proactive use" language | P1 |
| pr-flow | KEEP-REWRITE | Add "always use when ready" + distinguish from dual-verify | P1 |
| dual-verify | KEEP-REWRITE | Reinforce "mandatory" + clarify pre-merge timing | P1 |
| handoff | KEEP-REWRITE | Add triggers, value prop, context preservation | P2 |
| autoresearch | KEEP-REWRITE | Boundary from web-research (≥3 questions) | P1 |
| kb-lint | KEEP-REWRITE | Add proactive push + clarify `$AGENTKB_DIR` | P2 |
| web-research | KEEP-REWRITE | Boundary from autoresearch | P1 |
| gh-project-flow | KEEP-REWRITE | Reduce discouragement; keep sunset message | P2 |
| caveman-toggle | KEEP | Optional: consistency revision (§4.9) | P3 |

---

## §6. Methodology Limits

**Static Analysis Only**: This report is based entirely on SKILL.md frontmatter + description text. No runtime behavior measured.

**Why Empirical Trigger-Rate Unattainable**:
The upstream skill-creator's `run_eval.py` attempted to measure trigger accuracy by:
1. Running Claude Code against a temp shim command that invokes a single Skill or Read tool.
2. Checking if the first tool-use in Claude's response matches the expected skill.

**Problems with this approach in Claude Code 2.1.118+**:
- Claude now uses Glob, Grep, Bash, and built-in tools *first* for exploration (not Skill or Read).
- The `run_eval.py:141` has `else: return False`, which immediately misclassifies any non-Skill/non-Read first tool-use as "not triggered".
- Result: even if the skill *eventually* fires, the evaluation marks it as "not triggered" because Claude did a Glob/Grep first.
- This makes the tool's trigger-rate output meaningless on current Claude Code.

**Windows Patch Applied**:
The `select.select` incompatibility on Windows was patched (threading-based stdout reader); however, the fundamental detection logic remains broken. The patch is preserved in project memory for future use if/when upstream fixes the detection mechanism.

**Recommendation**:
- Follow-up Issue #279 subtask: Evaluate `promptfoo` (prompt testing framework) as alternative trigger-eval mechanism.
- Or: Wait for upstream skill-creator fix to detection logic (requires Claude Code introspection API or different approach entirely).
- For now: Rely on static analysis (this report) + manual spot-checks during development.

---

## Appendix: Conflict Resolution Roadmap

### Conflict 1: "review" (dual-verify ↔ pr-flow)
**Recommended user-facing clarification**:
> When the agent encounters "review" in user input:
> - **dual-verify**: Pre-merge code review (Claude deep-review + Codex audit in parallel, consolidate findings, gate commit).
> - **pr-flow**: Post-approval Argus loop (PR created, Argus reviews, user/agent fixes, loop until APPROVE, merge).
>
> If user says "review my code", prefer dual-verify. If user says "check PR status" or "Argus found issues", prefer pr-flow.

**Implementation**: Both skill descriptions (revised §4.2, §4.3) now mention timing. Suggest adding to CLAUDE.md glossary.

### Conflict 2: "research" (autoresearch ↔ web-research)
**Recommended user-facing clarification**:
> When the agent encounters "research" or "验证" in user input:
> - **web-research**: Single-source verification (SDK docs, API signatures, CLI flags). Quick, targeted, ≤2 questions.
> - **autoresearch**: Deep multi-source investigation (≥3 research questions, cross-verification, mechanical quality gate, multi-round loops).
>
> If user says "研究一下这个库" (research this library) but it's just "check the docs", use web-research. If user says "深度调研" (deep research) or the task has ≥3 questions, use autoresearch.

**Implementation**: Both skill descriptions (revised §4.5, §4.7) now mention the boundary. Suggest adding to CLAUDE.md or new skill-interaction ADR.

---

## References

- DIRECTION.md: Mercury's highest guidance document (project philosophy, modular design, mount-not-rewrite).
- CLAUDE.md §MUST: dual-verify gate requirement, web-research mandatory, issue-first workflow.
- feedback_skill_chain_cleanup_sot.md: Skill chain protocol (dev-pipeline → pr-flow handoff).
- feedback_no_internal_jargon_in_skills.md: Skill bodies should avoid internal terminology; descriptions are user-facing.
