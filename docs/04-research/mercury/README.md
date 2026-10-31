# Mercury research series

Mercury's own research, audits and ADR-style evaluations from 2026-04 onwards, current ones first. Moved here
from the Mercury repository in #675; relative links inside the older files were not rewritten, so look up their
referents by name. Subdirectories: [design/](design/README.md), [incidents/](incidents/README.md),
[articles/](articles/README.md).

| Document | Status | Content | Superseded by |
|---|---|---|---|
| [issue-158-architecture-evaluation.md](issue-158-architecture-evaluation.md) | current | Architecture direction: thin core plus mounted projects (basis of P1) |  |
| [context-strategy-2026-05.md](context-strategy-2026-05.md) | current | Context strategy against the 1M-context norm (ADR) |  |
| [settings-permissions-vs-hooks-2026-06.md](settings-permissions-vs-hooks-2026-06.md) | current | settings.json permissions versus PreToolUse hooks (#486 ADR) |  |
| [codex-hooks-adoption-2026-05.md](codex-hooks-adoption-2026-05.md) | current | Codex CLI hooks adoption |  |
| [issue-571-codex-migration-2026-08.md](issue-571-codex-migration-2026-08.md) | current | Moving Mercury's workflow to Codex CLI |  |
| [issue-579-native-harness-audit-2026-09.md](issue-579-native-harness-audit-2026-09.md) | current | Codex native harness audit and minimal adjustments |  |
| [issue-579-detachable-harness-candidates-2026-09.md](issue-579-detachable-harness-candidates-2026-09.md) | current | Detachable harness candidates |  |
| [issue-599-cross-harness-lane-isolation-2026-09.md](issue-599-cross-harness-lane-isolation-2026-09.md) | current | Codex and Claude cross-harness lane isolation (ADR) |  |
| [issue-517-slim-audit-2026-06.md](issue-517-slim-audit-2026-06.md) | current | Slimming audit; retired mem0 and other unused parts |  |
| [issue-525-zh-output-readability-2026-06.md](issue-525-zh-output-readability-2026-06.md) | current | Chinese output readability: causes and fixes |  |
| [issue-535-fable5-scheduling-2026-07.md](issue-535-fable5-scheduling-2026-07.md) | current | Weekly quota scheduling and statusline display |  |
| [image-api-skill-2026-10.md](image-api-skill-2026-10.md) | current | General image API skill |  |
| [issue-154-web-automation-2026-05.md](issue-154-web-automation-2026-05.md) | current | Web automation via pinned Playwright MCP (pack currently disabled) |  |
| [issue-157-external-info-agent-2026-05.md](issue-157-external-info-agent-2026-05.md) | current | External information watch agent |  |
| [issue-159-permissions-research.md](issue-159-permissions-research.md) | current | Permission blocking in unattended mode |  |
| [issue-289-design-routines-2026-05.md](issue-289-design-routines-2026-05.md) | current | Claude Design and Claude Code Routines |  |
| [ecc-practice-scan-2026-06.md](ecc-practice-scan-2026-06.md) | current | everything-claude-code practice review |  |
| [harness-modernization-survey-2026-06.md](harness-modernization-survey-2026-06.md) | current | Absorbing 2026 Q1-Q2 Claude Code orchestration primitives |  |
| [agent-view-multi-lane-adaptation-2026-05.md](agent-view-multi-lane-adaptation-2026-05.md) | current | Agent view and the multi-lane protocol |  |
| [codex-rescue-async-semantics-2026-04-27.md](codex-rescue-async-semantics-2026-04-27.md) | history | Codex rescue async semantics for the dual-verify skill (removed in #666/#667) |  |
| [cma-memory-vs-mem0-2026-05.md](cma-memory-vs-mem0-2026-05.md) | superseded | CMA memory versus mem0 (premise disproved; mem0 retired) | [issue-517-slim-audit-2026-06.md](issue-517-slim-audit-2026-06.md) |
| [hook-layer-modernization-2026-05.md](hook-layer-modernization-2026-05.md) | history | Hook layer audit of 2026-05 |  |
| [memory-layer-rebuild-2026-04-16.md](memory-layer-rebuild-2026-04-16.md) | history | Memory layer selection that led to mem0 (retired in #518) |  |
| [agentkb-fork-salvage-audit-2026-04-17.md](agentkb-fork-salvage-audit-2026-04-17.md) | history | AgentKB fork salvage audit |  |
| [multi-lane-protocol-2026-04-25.md](multi-lane-protocol-2026-04-25.md) | history | Multi-lane protocol v0 validation |  |
| [agent-team-orchestration-feasibility-2026-04-26.md](agent-team-orchestration-feasibility-2026-04-26.md) | history | Director and side-lane team orchestration study |  |
| [issue-155-multi-team-architecture-2026-05.md](issue-155-multi-team-architecture-2026-05.md) | history | Director-manager-employee multi-team design |  |
| [issue-462-phase2-poc-2026-05-27.md](issue-462-phase2-poc-2026-05-27.md) | history | Lane-as-process PoC results |  |
| [agent-view-phase6-empirical-2026-05.md](agent-view-phase6-empirical-2026-05.md) | history | Agent view evidence for the GUI phase |  |
| [phase6-gui-mvp-agentview-backend-schema-2026-05.md](phase6-gui-mvp-agentview-backend-schema-2026-05.md) | history | GUI MVP data source schema (GUI removed in #645) |  |
| [phase6-gui-mvp-tech-stack-2026-05.md](phase6-gui-mvp-tech-stack-2026-05.md) | history | GUI MVP tech stack (GUI removed in #645) |  |
| [issue-438-frontend-test-infra-2026-05.md](issue-438-frontend-test-infra-2026-05.md) | history | GUI frontend test infrastructure proposal |  |
| [issue-446-mercury-gui-structural-split-2026-05.md](issue-446-mercury-gui-structural-split-2026-05.md) | history | GUI structural split proposal |  |
| [issue-86-pr-monitor-audit-2026-05.md](issue-86-pr-monitor-audit-2026-05.md) | history | PR monitor proposal audit (pr-flow removed in #672) |  |
| [loop-detector-fix-2026-04-27.md](loop-detector-fix-2026-04-27.md) | history | Loop detector false-positive fix (adapter removed in #666) |  |
| [phase2-1-get-shit-done-evaluation.md](phase2-1-get-shit-done-evaluation.md) | history | Phase 2-1 evaluation of get-shit-done |  |
| [phase2-1-omc-evaluation.md](phase2-1-omc-evaluation.md) | history | Phase 2-1 evaluation of oh-my-claudecode |  |
| [phase2-1-openspace-evaluation.md](phase2-1-openspace-evaluation.md) | history | Phase 2-1 evaluation of OpenSpace |  |
| [phase2-1-superpowers-evaluation.md](phase2-1-superpowers-evaluation.md) | history | Phase 2-1 evaluation of superpowers |  |
| [phase4-1-design-decisions-2026-04-13.md](phase4-1-design-decisions-2026-04-13.md) | history | Session continuity design decisions |  |
| [phase4-1-session-continuity-adr-draft.md](phase4-1-session-continuity-adr-draft.md) | history | Session continuity technical options (draft ADR) |  |
| [phase4-2-worktree-mount-eval.md](phase4-2-worktree-mount-eval.md) | history | Worktree-per-task mount evaluation; current rules in 02-guides |  |
| [phase4-3-compact-prevention-eval.md](phase4-3-compact-prevention-eval.md) | history | Compact prevention research |  |
| [skill-eval-279-static-report.md](skill-eval-279-static-report.md) | history | Static evaluation of the skills of that time |  |
