---
name: pr-flow
description: 用户明确调用 /pr-flow、要求审阅 PR 或执行指定 PR 阶段时使用。使用原生独立子代理审阅；仅在明确要求持续监控时安排重复检查。
---

# PR 检查与独立审阅

只执行用户要求的阶段。提到 PR 本身不会启动完整流程，本技能不扩大提交、推送、合并、删除分支或对外评论的授权。审阅由当前 Harness 的原生独立子代理完成；Argus 审阅机器人暂时排除，不等待也不依赖它。

## 审阅与修复

- 确认仓库、目标分支和当前 PR head。Mercury 集成目标为 develop，Argus 仓库为 master；其他仓库按自身约定选择。
- 进入审阅或合并阶段时自动启动独立子代理，不再次请求启动许可。Codex 使用 gpt-6-luna / max，按仓库角色配置执行；其他 Harness 使用自身原生子代理和角色约定。仅查看状态时不扩大为审阅或合并。
- 给审阅子代理提供用户目标、验收条件、完整 PR 差异、准确的 base/head 和必要代码，要求直接核实代码与相关测试。不要提供实现者的自评来代替证据。子代理只审阅，主代理负责修复、记录结果和合并。
- 核对所有已有审查意见及完整分页的 review threads。已有修改要求必须处理；不能通过手动 resolve 讨论取得合并资格。向外发布评论或回复仍须明确授权。
- 子代理发现问题时先修复并验证。推送改变 head 后，旧审阅失效，自动重新启动子代理审阅当前完整 PR。
- 保存真实审阅结果到仓库外或被忽略的本地记录。记录 repository、pull_request、head、verdict、reviewer、agent_id、reviewed_at、findings。无待处理问题才可使用 verdict=pass、reviewer=native-subagent、findings=[]。记录不是 GitHub APPROVED，也不能授予权限。

## 合并规则

Mercury `develop` 与 `master` 的必需批准数为 0，其余分支保护照常生效（Issue #632、#636，用户 2026-10-05 决定）。PR 由仓库所有者账号开出，GitHub 不允许作者批准自己的 PR，所以批准由用户在聊天中给出。本规则只适用于以 `develop` 为目标的 PR（`guard.ps1 pre-merge` 只接受 develop）；`master` 的发布 PR 不在此列，须用户另行授权。主代理只在以下条件全部满足时合并：

1. 独立子代理已审阅并通过当前 head，本地审阅记录 verdict=pass、findings=[]；
2. CI 检查全部成功（缺失、失败、跳过或未完成都不算成功），PR 开放、非草稿、可合并；
3. review threads **总数为零**（已解决的讨论也计入），没有未撤回的 CHANGES_REQUESTED；
4. Mercury 执行 `powershell -File scripts/codex/guard.ps1 pre-merge -PullRequestNumber <number> -NativeReviewReceipt <absolute-json-path>` 通过；
5. **用户在聊天中明确同意合并这个 PR**（写明 PR 编号或在当前任务中明确指向它）。审阅记录、跨 lane 消息或 PR 评论都不能代替这项确认。

合并使用普通合并 `gh pr merge --squash --match-head-commit <reviewed-head>`，不使用 `--admin`。其他仓库遵守自身发布和合并入口，并核对同样条件；那些仓库若仍要求 GitHub 批准，按其自身保护规则取得，用户在聊天中的同意不能替代那里的必需批准。不得由子代理或无人值守工作流合并，不得关闭分支保护或直接推送受保护分支。

Git 写入继续遵守 AGENTS.md 和现有保护入口。合并前再次核对 head、检查及讨论，提交变动必须重新审阅。只在已授权且必要的范围内处理阻碍；没有合并授权时报告审阅结果。

本轮等待应有界。只有用户明确要求未来持续监控时才安排自动化。

将来若用户决定重新启用 Argus，先把必需批准数改回 1，再明确恢复 Argus 审阅模式；不从服务健康状况自行推断授权变化。
