---
name: pr-flow
description: 用户明确要求检查 PR、处理 Argus 审查意见或执行某个 PR 阶段时使用；只做本轮指定阶段。
user-invocable: true
---

# PR 与 Argus 审查

本技能不会因提到 PR 而自动启动完整流程，不扩大评论、提交、推送、合并或删除分支的授权。

- 按仓库约定确认目标分支。Mercury 使用 develop；Argus 仓库使用 master。其他仓库遵循其文档或既定流程，不能仅因 develop 存在就选择它。
- 查看当前 PR 状态、head、reviewDecision、最新相关 Argus review 和未处理意见。报告结论时说明它对应的 head。
- 阅读相关意见，区分需要修复、不同意或仍需澄清的事项。不要手动 resolve Argus review thread；由修复检测或 Argus 回复流程更新状态。
- 推送修复后，旧的 APPROVED 不代表新 head 已通过。核对针对当前 head 的最新 review，再依据 reviewDecision 报告状态。
- 依据当前任务、已存在的用户授权和仓库约定执行对应阶段。技能本身不扩大授权。创建或修改 PR、提交、推送、合并或删除分支，仅在当前授权覆盖该动作时执行；对外发布评论或回复需要明确授权。合并还须满足仓库要求的审批、检查和未解决意见条件。
- Git 写入仍须遵守 AGENTS.md，并使用 scripts/codex/git-safe.ps1 的相应保护入口。不得用直接 Git 写操作绕过仓库保护。
- 若审查或检查尚未就绪，报告已完成内容、待处理依赖和当前证据。只有用户明确要求未来持续监控时，才使用当前环境支持的自动化；否则不安排后续轮询。
