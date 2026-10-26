# html-plan-zh 上游来源

- 上游：[anthropics/claude-plugins-community](https://github.com/anthropics/claude-plugins-community) 的 `html-plan/` 插件，作者 Thariq Shihipar。
- 导入版本：`88003be1129c09381d27100b37a8fdc21b3214bf`（2026-10-05，"html-plan: calmer page style"）。
- 许可：插件清单 `html-plan/.claude-plugin/plugin.json` 声明 `MIT`，上游插件没有单独的版权行或 MIT 许可文件；仓库整体按 Apache License 2.0 发布。本目录的 `LICENSE` 写明这两点和本目录的改动，并逐字附上仓库 LICENSE 全文。从上游复制的文件在头部（`SKILL.md` 在 frontmatter）注明上游来源和提交，具体改动见下面的改动表。
- 导入记录：Mercury Issue #655；每个文件的条目见 `.mercury/state/upstream-manifest.json`。

## 本目录相对上游的改动

| 文件 | 改动 |
|---|---|
| `SKILL.md` | 译为中文；“行文”规则由 ASD-STE100 简化技术英语改为简明简体中文；字数阈值按中文字数重定；回复格式改为中文；补充用户级副本位置与 artifact 发布前的隐私检查；description 与正文加入非代码设计提案的用法指引（#661） |
| `references/blocks.md` | 正文与上游一致，仅在顶部加中文说明（补丁 `adapters/html-plan/blocks.md.patch`） |
| `references/design-proposals.md` | 本目录新增（#661），上游无对应文件：非代码设计提案的论断树与证据块用法；Mercury 自写，不登记 manifest |
| `LICENSE` | 许可说明头部加上游仓库 LICENSE 原文 |
| `examples/scheduled-send.html` | 行文、界面示意与决策选项译为中文；代码与结构不变 |
| `runtime/htmlplan.js` | 用户可见界面文案与回复 markdown 中文化（由 `adapters/html-plan/` 生成） |
| `runtime/htmlplan.css` | 补中文字体回退（由 `adapters/html-plan/` 生成） |
| `runtime/pack.mjs` | 长度检查改为按中文字数计算；英文 STE 用词检查换成中文用词检查（由 `adapters/html-plan/` 生成） |

## 更新上游

本目录由 `adapters/html-plan/generate.py` 从子模块 `modules/claude-plugins-community` 生成，不要手改。维护步骤见 `adapters/html-plan/UPSTREAM.md`。

## 原版插件的版本固定

Mercury 的 `.claude/settings.json` 声明 `claude-community` 市场并启用 `html-plan@claude-community`。社区市场里该插件的来源是仓库内相对路径 `./html-plan`，内容随市场仓库变化。为避免上游新增 hooks、MCP 或命令后在本仓库自动生效，项目设置对该市场写明 `"autoUpdate": false`：已安装的插件停在安装时的版本（安装时核对为 `88003be`，只含 skill，不含 hooks 或 MCP），不在后台更新。注意：这只约束已安装的副本；新克隆本仓库的人首次安装时，拿到的是当时市场里的最新内容，安装后同样先按下面的步骤核对。

需要升级时：先查看上游 `html-plan/` 自上次核对以来的 diff，确认 `plugin.json` 和目录里没有新增 hooks、MCP、命令或可执行内容，再手动运行 `claude plugin update html-plan@claude-community`，并在 Mercury Issue 记录核对结果。
