# KB 结构

Mercury 的知识库是本仓库的 `docs/`，同时是一个 Obsidian vault。它在 #718 中由私有的 Mercury_KB vault 以审计后的当前快照并入；旧仓库保持私有并归档，保存完整历史和未公开的材料。2026-10-31 起（#736）它遵循 kb pack 的约定（`.mercury/packs/kb/docs/kb-conventions.md`），原 `.mercury/docs/` 的指南并入 `docs/02-guides/`；原方向文档中仍有效的部分收编进 `01-rules/principles.md`，其余过时内容已删除。

| 路径 | 内容 |
|------|------|
| `docs/README.md` | 入口：现行规则 → 当前进度 → 决策登记；根目录只放这一个文件 |
| `docs/00-progress/` | 当前进度：未完成的总 Issue 和下一步 |
| `docs/01-rules/` | 现行规则；`principles.md` 收编自原 `DIRECTION.md` |
| `docs/02-guides/` | 工作指南：Git、worktree、Issue、上游导入、本文件 |
| `docs/03-decisions/` | 决策登记（`README.md`）与仍然有效的决策记录 |
| `docs/04-research/` | 研究报告；`mercury/` 是 Mercury 专题系列，含 `articles/`、`incidents/` |

## 维护规则

- **每个目录有索引**：目录的 `README.md` 说明用途和先读哪篇，并在带“Status/状态”列的表格里列出本目录每篇文档的状态，只有 `current`（现行）和 `draft`（草稿）两种。新增文档时同一 PR 加一行。
- **随任务一起推进**：研究、决策和设计说明与它解释的代码改动在同一个任务 worktree 中修改、在同一个 PR 中交付。PR 描述写 `KB:` 行，列出更新或删除的文档，没有影响就写 `KB: no impact` 和理由；`guard.ps1 pre-merge` 会检查这一行。
- **过时即删除**：改动规则、决策或进度时，在同一 PR 里更新 `01-rules/`、`00-progress/` 或 `03-decisions/README.md`，并删除因此过时或错误的文档、规则和登记行，连同指向它们的链接。不保留“已取代”或“历史”标记，也不设归档目录：代理靠搜索和链接找文档，看不到状态标记，留下的旧文会被当成现行内容读到。旧文需要引用时，用固定提交的永久链接（`https://github.com/392fyc/Mercury/blob/<sha>/<路径>`）；用户原话写进对应的 Issue 或 PR。
- **检查**：`python -B .mercury/packs/kb/scripts/kb_check.py docs`，CI 在每个 PR 上运行。它检查每篇文档里的每个链接（包括 `[[wikilink]]`），指向已删除文件的链接会报错。
- **读写方式**：代理直接读写 `docs/` 下的文件。Obsidian 打开主 checkout 的 `docs/` 浏览；Obsidian MCP 指向主 checkout，只看得到已合并内容，代理不经它写入。在 Obsidian 中编辑时，关掉“设置 → 文件与链接 → `Use [[Wikilinks]]`”，让它写标准 Markdown 链接。
- **公开边界**：本仓库公开。`docs/` 中不得出现凭据、本机绝对路径（用 `<repo-root>`、`<workspace>` 等占位）、家庭或私有基础设施细节、私有项目内容和个人信息。`docs/.obsidian/` 整体被 Git 忽略（插件 `data.json` 含 API 密钥）。
- **引用写法**：`docs/` 内提到 `docs/` 以外的仓库文件时写代码格式的仓库相对路径（如 `AGENTS.md`、`packs/README.md`），不写 Markdown 链接，因为 Obsidian 只能打开 vault 内的文件；Mercury 自身其他文件用相对链接指向 `docs/<路径>`；会被安装到下游项目的 pack 文档写 “Mercury `docs/<路径>`”，避免与下游项目自己的 `docs/` 混淆。
- **研究过程状态**：研究流程的机器状态写在被忽略的 `.research/state/`，不进入 `docs/`。

## 交接文档

交接（handoff）文档是临时工作态，不进入 `docs/`。主 checkout 的 `.handoff-config` 不再设置 `kb_dir`，lanes pack 回退到被忽略的 `<主 checkout>/.handoff/`。
