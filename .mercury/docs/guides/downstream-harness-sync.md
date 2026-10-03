# 下游 Harness 的单向更新

Mercury 是可移植 Harness 的唯一来源。下游按模板 `manifest.json` 消费
`.codex/agents/mercury-*`、`.codex/rules/mercury-*`、
`.codex/project/mercury-*` 以及 `.codex/mercury-template.lock`。
下游代理不得修改或发起这些文件的内容变更；需求和修复由 Mercury Issue/PR 发起。
下游自己的 agent、技能、配置、领域规则、项目记忆和工作流程使用其他路径。

`scripts/codex/verify-project-template.py` 只读验证锁的规范形式、可信上游提交
及生成内容。`sync-project-template.py` 只写清单路径；发现下游修改即停止，
不会把修改覆盖掉。移除路径同样核对旧锁。保留现有工作树和用户配置。

## SoT 的自动更新入口

SoT 的 `.github/workflows/mercury-sync.yml` 是下游调度入口：它从 Mercury
`develop` 检出已合入来源，在隔离、干净的 SoT `develop` checkout 中运行
`scripts/codex/sync-downstream-pr.py`。程序比较模板载荷，载荷未变时不为
Mercury 的其他提交生成新 PR；载荷变化时生成仅含受管文件的任务 PR。

下一次调度先检查已有更新 PR，不覆写其分支，不另建重复 PR。合并前从 Git
对象核对清单、文件模式、精确字节、来源提交及升级方向；PR 的所有改动必须
属于旧、新清单的并集。当前工作流使用 `--publish-only`：自动发布和验证 PR，
由主代理调用 `/pr-flow` 自动启动原生独立子代理审阅并按当前规则合并。
定时工作流不执行主代理的临时旁路，也不把本地审阅记录视为 GitHub 批准。

显式恢复 Argus 模式并移除 `--publish-only` 后，程序须有
`argus-review[bot]` 对当前 head 的有效批准，全部
已报告检查成功，而且没有其他审查者未撤回的修改要求。没有检查、旧批准、
失败、跳过、未完成、冲突或草稿均停止合并。发布走 SoT 的受控入口；合并
使用普通 PR API 并绑定当前 head，不用管理员绕过或受保护分支直推。

调度入口与 `ci.yml` dispatch 入口须通过单独 PR 安装到 SoT 默认分支
`main`；同步目标为 `develop`。默认分支的 CI 入口调用 `develop` 的可复用
CI；任务分支上的 dispatch 则运行该分支完整的 CI 文件。
`develop` 上保留同一入口以便手动执行和后续集成。PR 来源验证由独立的
`.github/workflows/mercury-template-verify.yml` 执行，避免定时发布任务在 PR
上跳过而阻止合并。该验证只读取可信 Mercury 来源和精确 PR head。

## 认证与运行条件

工作流默认使用该仓的 `GITHUB_TOKEN`，权限限定为内容、PR、Actions 写入，
以及 Checks、Commit statuses 读取，以核对当前提交的全部检查结果。
仓库必须允许 Actions 创建 PR；脚本不使用批准审查权限。
若配置可选秘密 `MERCURY_SYNC_TOKEN`，应使用仅限目标仓的 GitHub App
安装令牌或细粒度 PAT，具备 Contents、Pull requests、Actions 写入，
以及 Checks、Commit statuses 读取权限。
凭据通过 GitHub Secrets 本地配置，不进入仓库、回执或聊天。

GitHub 文档说明 `GITHUB_TOKEN` 创建的 PR 检查可能需要人工批准；显式
`workflow_dispatch` 可以触发运行。App/PAT 能使常规 PR CI 自动触发。
没有相应运行结果和当前 head 的独立审阅时，管线只保留待处理 PR。
私有仓当前套餐不提供原生自动合并时，本程序仍可在上述条件满足后调用
普通 PR 合并。GitHub Free 私有仓也不支持分支保护或 rulesets；因此这里的
审查和 CI 条件由同步脚本检查，服务端仅核对合并时的 head，不能阻止其他
有写权限的人绕过脚本合并。检查列表超过已验证的 100 项时，脚本停止合并。
它不会修改 GitHub 套餐、分支保护或默认分支。

来源：[GitHub 工作流触发规则](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)、
[自动合并的适用套餐](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-auto-merge-for-pull-requests-in-your-repository)、
[分支保护的适用套餐](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)。

## 停用与恢复

停用下游定时工作流即可停止后续更新；已安装生成文件继续按记录的版本生效。
删除可选令牌或撤销权限不会改变已合入内容。待处理 PR 保留，禁止强制覆盖。
若发布中断留下未建立 PR 的任务分支，先核对它的树和来源，再人工恢复 PR；
程序不会强推或删除该分支。检查失败须在 Mercury 来源或 SoT 自有覆盖层中
修复对应问题，再重新验证；不要在下游手改生成文件。
