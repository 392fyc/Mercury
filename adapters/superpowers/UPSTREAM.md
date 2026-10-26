# superpowers 适配层：上游来源

- 上游仓库：[obra/superpowers](https://github.com/obra/superpowers)，路径 `skills/subagent-driven-development/`。
- 子模块：`modules/superpowers`，钉住提交 `d884ae04edebef577e82ff7c4e143debd0bbec99`（v6.1.1，2026-07-02）。
- 许可证：MIT（Copyright 2025 Jesse Vincent）。
- 导入记录：Mercury Issue #209 / PR #216（2026-04-10，初次导入自 `917e5f53b16b115b70a3a355ed5f4993b9f8b73d`）、Issue #509（2026-07-04，选择性回移 v6.1.1 的“报告写入文件”约定）、Issue #668（改为子模块与适配层）。
- 输出位置：`harness/modules/subagent-dev/skills/mercury-subagent-driven-development/`（由 `generate.py` 生成，不手改；构建器再投影到 `.agents/skills/`）。

## 文件归类

四个文件都是 Mercury 的改写或自写，与钉住提交的上游文件差异极大（见下），因此整篇放 `overlay/`，不存补丁：

| 输出文件 | 与上游的关系 |
|---|---|
| `SKILL.md` | Mercury 自写的中文流程（48 行），上游同名文件 418 行；仅沿用“每个任务用新子代理”的思路，文件头注明出处 |
| `implementer-prompt.md` | 以初次导入的 `917e5f5` 为底，局部回移 `d884ae0` 的报告文件约定；与钉住提交的同名文件（139 行）仅约 9 行相同 |
| `spec-reviewer-prompt.md`、`code-quality-reviewer-prompt.md` | 取自 `917e5f5`，仅加文件头注明出处；这两个文件在 `d884ae0` 已被上游删除（合并为 `task-reviewer-prompt.md`），所以钉住的子模块里没有对应原文。Mercury 有意保留两段式审查 |

`generate.py` 对 `implementer-prompt.md` 检查两个上游原文片段仍存在于钉住提交（防止出处悄悄改变）；两个审查提示词没有可检查的原文，仅在此说明。

## 命令

```
python adapters/superpowers/generate.py           # 写出
python adapters/superpowers/generate.py --check   # 比对；有差异时退出 1 并列出文件
```

## 升级步骤（另开 PR）

1. 在子模块里检出新提交，`git add modules/superpowers`，同步更新 `generate.py` 的 `PIN`。
2. 查看上游 `skills/subagent-driven-development/` 自钉住提交以来的差异，决定哪些要回移到 `overlay/`；确认没有新增 hooks 或可执行脚本被带入。
3. 运行 `generate.py --check`，锚点失效时按上游新文本调整 `ANCHORS`。
4. 更新 `overlay/SKILL.md` frontmatter 的 `backported_*` 字段与 manifest，经独立审查后提交。
