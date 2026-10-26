---
name: mercury-subagent-driven-development
description: >-
  仅在用户明确要求或既定计划选用多步骤子代理开发时使用。以 Codex 原生能力组织有界的 Luna Max 实现、风险相称的独立审查和可恢复的简要进度。
user-invocable: true
upstream_source: "https://github.com/obra/superpowers"
upstream_sha: "917e5f53b16b115b70a3a355ed5f4993b9f8b73d"
upstream_license: "MIT"
cherry_picked_in: 216
cherry_picked_at: "2026-04-10"
backported_from_sha: "d884ae04edebef577e82ff7c4e143debd0bbec99"
backported_from_tag: "v6.1.1"
backported_in: 509
backported_at: "2026-07-04"
mercury_adaptation: >-
  按需使用 Codex 原生能力：Astra 主代理负责范围与整合，Luna Max 执行有界任务；独立审查和进度记录按风险与计划选择。辅助 prompt 仅作兼容时的可选参考，不覆盖当前任务或仓库约定。
---

<!-- Cherry-picked from obra/superpowers (MIT, Copyright 2025 Jesse Vincent)
     Initial import: https://github.com/obra/superpowers/blob/917e5f5/skills/subagent-driven-development/SKILL.md
     Initial SHA: 917e5f53b16b115b70a3a355ed5f4993b9f8b73d (2026-04-10, Issue #209)
     Selective back-port: obra/superpowers v6.1.1 (commit d884ae04edebef577e82ff7c4e143debd0bbec99, 2026-07-02)
     Back-port SHA / Issue: d884ae0 / #509 (2026-07-04)
     This is a Mercury-owned adaptation, not a verbatim mirror. -->

# Codex 原生多步骤开发

仅在用户明确要求此工作流，或既定计划要求多步骤子代理执行时使用。单项、简单任务可直接完成。

## 执行

1. 阅读当前用户要求、仓库约定和选定计划。拆分为路径、目标和验收条件明确的任务；只执行已授权范围。
2. Astra 主代理负责规划、关键决策和整合。按任务范围派发 Luna Max，在隔离上下文中一次完成一个有界实现任务；保留并尊重工作区中其他人的改动。
3. 按风险和仓库规则核验结果。复杂行为、安全、权限、跨仓库或 agent 规则改动需要独立上下文审查；简单改动按相称的项目原生检查验证。无需每项任务都经过固定的两段审查。
4. 对可能跨轮次或中断的计划，可在仓库忽略的临时目录记录简短进度：已完成项、当前状态、下一项和必要证据。恢复时先核对记录与当前文件和 Git 状态；短任务无需创建记录。
5. 返回改动、验收证据、检查结果和剩余事项。工作流本身不会自动提交、推送、创建 PR、改动远程 Issue 或删除工作内容；需要这些动作时，依据当前任务和会话中已有授权，并按仓库规则执行。

遇到计划与当前用户要求或仓库约定冲突、任务超出授权范围时，停止受影响部分并说明原因。不可逆、跨仓库或影响共享基础设施的决策仍须先确认；范围内的常规步骤不需要额外确认。

## 可选 prompt 参考

需要撰写子任务指令或审查清单时，可按需参考：

- [implementer-prompt.md](implementer-prompt.md)
- [spec-reviewer-prompt.md](spec-reviewer-prompt.md)
- [code-quality-reviewer-prompt.md](code-quality-reviewer-prompt.md)

这些文件不是必经阶段。仅在适用于当前 Codex 环境和任务时读取；如与当前用户要求、AGENTS.md 或可用工具冲突，以当前要求和仓库约定为准。
