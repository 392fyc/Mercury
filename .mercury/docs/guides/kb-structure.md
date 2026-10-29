# KB 结构

Mercury 的知识库是本仓库的 `docs/`，同时是一个 Obsidian vault。它在 #718 中由私有的 Mercury_KB vault 以审计后的当前快照并入；旧仓库保持私有并归档，保存完整历史和未公开的材料。

| 路径 | 内容 |
|------|------|
| `docs/00-index/` | 入口说明与决策索引 |
| `docs/01-registry/` | 长期有效的策略 |
| `docs/03-decisions/` | 架构决策记录（ADR） |
| `docs/03-research/` | 早期（phase 3）研究报告 |
| `docs/04-research/` | 研究报告；`mercury/` 子目录是 Mercury 专题系列，含 `articles/`、`design/`、`incidents/` |

## 维护规则

- **随任务一起推进**：研究、决策和设计说明与它解释的代码改动在同一个任务 worktree 中修改、在同一个 PR 中交付。新增 ADR 时同步更新 `docs/00-index/decisions-index.md`。
- **读写方式**：代理直接读写 `docs/` 下的文件。Obsidian 打开主 checkout 的 `docs/` 浏览；Obsidian MCP 指向主 checkout，只看得到已合并内容，代理不经它写入。
- **公开边界**：本仓库公开。`docs/` 中不得出现凭据、本机绝对路径（用 `<repo-root>`、`<workspace>` 等占位）、家庭或私有基础设施细节、私有项目内容和个人信息。`docs/.obsidian/` 整体被 Git 忽略（插件 `data.json` 含 API 密钥）。
- **引用写法**：`docs/` 内提到 `docs/` 以外的仓库文件时写代码格式的仓库相对路径（如 `.mercury/docs/DIRECTION.md`），不写 Markdown 链接，因为 Obsidian 只能打开 vault 内的文件；Mercury 自身其他文件用相对链接指向 `docs/<路径>`；会被安装到下游项目的 pack 文档写 “Mercury `docs/<路径>`”，避免与下游项目自己的 `docs/` 混淆。
- **研究过程状态**：研究流程的机器状态写在被忽略的 `.research/state/`，不进入 `docs/`。

## 交接文档

交接（handoff）文档是临时工作态，不进入 `docs/`。主 checkout 的 `.handoff-config` 不再设置 `kb_dir`，lanes pack 回退到被忽略的 `<主 checkout>/.handoff/`。

## 项目内其他文档

Harness 自身的文档留在 `.mercury/`：

| 路径 | 内容 |
|------|------|
| `.mercury/docs/DIRECTION.md` | 发展方向 |
| `.mercury/docs/EXECUTION-PLAN.md` | 执行计划 |
| `.mercury/docs/guides/git-flow.md` | Git 分支规范 |
| `.mercury/docs/guides/kb-structure.md` | 本文件 |
