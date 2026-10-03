---
name: pr-flow
description: 用户明确调用 /pr-flow、要求审阅 PR 或执行指定 PR 阶段时使用。当前临时使用原生独立子代理审阅；仅在明确要求持续监控时安排重复检查。
---

# PR 检查与独立审阅

读取并执行仓库根目录 `.agents/skills/pr-flow/SKILL.md` 的当前操作规则。该文件是两个 Harness 共用的规则来源；本入口不增加权限或另设审阅流程。Claude Code 使用自己的原生独立子代理及角色配置。
