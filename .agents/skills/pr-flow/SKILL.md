---
name: pr-flow
description: 用户明确调用 /pr-flow、要求审阅 PR 或执行指定 PR 阶段时使用。当前临时使用原生独立子代理审阅；仅在明确要求持续监控时安排重复检查。
---

# PR 检查与独立审阅

只执行用户要求的阶段。提到 PR 本身不会启动完整流程，本技能不扩大提交、推送、合并、删除分支或对外评论的授权。当前 Argus 的模型凭据暂不可用，默认自动启动当前 Harness 的原生独立子代理审阅，不等待 Argus。

## 审阅与修复

- 确认仓库、目标分支和当前 PR head。Mercury 集成目标为 develop，Argus 为 master；其他仓库按自身约定选择。
- 进入审阅或合并阶段时自动启动独立子代理，不再次请求启动许可。Codex 使用 gpt-6-luna / max，按仓库角色配置执行；其他 Harness 使用自身原生子代理和角色约定。仅查看状态时不扩大为审阅或合并。
- 给审阅子代理提供用户目标、验收条件、完整 PR 差异、准确的 base/head 和必要代码，要求直接核实代码与相关测试。不要提供实现者的自评来代替证据。子代理只审阅，主代理负责修复、记录结果和合并。
- 核对所有已有审查意见及完整分页的 review threads。已有修改要求必须处理；不能通过手动 resolve 讨论取得旁路资格。向外发布评论或回复仍须明确授权。
- 子代理发现问题时先修复并验证。推送改变 head 后，旧审阅失效，自动重新启动子代理审阅当前完整 PR。
- 保存真实审阅结果到仓库外或被忽略的本地记录。记录 repository、pull_request、head、verdict、reviewer、agent_id、reviewed_at、findings。无待处理问题才可使用 verdict=pass、reviewer=native-subagent、findings=[]。记录不是 GitHub APPROVED，也不能授予权限。

## 当前临时合并规则

用户已允许主代理在没有 review threads 时 bypass merge。仅在当前任务授权包含合并、独立子代理已通过当前 head、CI 检查成功、PR 开放且非草稿并可合并、没有未撤回的 CHANGES_REQUESTED，并且 review threads **总数为零**时使用此例外。已解决的讨论也计入线程总数。CI 缺失、失败、跳过或未完成不能充当成功。

Mercury 先执行 `powershell -File scripts/codex/guard.ps1 pre-merge -PullRequestNumber <number> -NativeReviewReceipt <absolute-json-path>`。其他仓库遵守自身发布和合并入口，并核对同样条件。服务端只因缺少批准阻止时，主代理可使用 `gh pr merge --squash --admin --match-head-commit <reviewed-head>`；普通合并可用时优先普通合并。不得由子代理或无人值守工作流实施此旁路，不得关闭分支保护或直接推送受保护分支。

Git 写入继续遵守 AGENTS.md 和现有保护入口。合并前再次核对 head、检查及讨论，提交变动必须重新审阅。只在已授权且必要的范围内处理阻碍；没有合并授权时报告审阅结果。

本轮等待应有界。只有用户明确要求未来持续监控时才安排自动化。恢复 Argus 时须明确恢复审阅模式，再删除临时旁路入口的使用；不自动从服务健康推断授权变化。
