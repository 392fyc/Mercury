# caveman 适配层：上游来源

- 上游仓库：[JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman)，路径 `skills/caveman/SKILL.md`。
- 子模块：`modules/caveman`，钉住提交 `26c25e39b3aee469dbb017427ab55ee1d32de1a8`（v1.3.0 同步提交）。
- 许可证：该提交的 `LICENSE` 为 MIT（Copyright 2026 Julius Brussee）。上游自 2026-09-24 的提交 `921eab8a` 起整体改为 Apache-2.0；升级子模块时要重新核对并更新 manifest。
- 导入记录：Mercury Issue #213 / PR #214（2026-04-10）、Issue #668（改为子模块与适配层）。
- 输出位置：`harness/modules/caveman/skills/caveman-toggle/SKILL.md` 与 `harness/modules/caveman/files/CLAUDE.local.md.example`（由 `generate.py` 生成，不手改；harness 构建器再投影到 `.agents/skills/` 与仓库根）。

## 文件归类

两个文件都是 Mercury 自写，整篇放 `overlay/`（路径与 `harness/modules/caveman/` 下的相对路径一致）：

| 输出文件 | 与上游的关系 |
|---|---|
| `skills/caveman-toggle/SKILL.md` | 开关技能（`/caveman-on`、`/caveman-off`、`/caveman-status`，管理 `CLAUDE.local.md`），上游没有对应功能；三档强度的提示词措辞改写自上游 `SKILL.md` 的规则与强度表 |
| `files/CLAUDE.local.md.example` | `CLAUDE.local.md` 的注入模板；`lite` 档文字改写自上游，项目例外条款为 Mercury 自写 |

提示词是改写而非逐字抽取，所以没有补丁或机器转换。`generate.py` 检查两条上游原文片段仍存在于钉住提交。

## 命令

```
python adapters/caveman/generate.py           # 写出
python adapters/caveman/generate.py --check   # 比对；有差异时退出 1 并列出文件
```

## 升级步骤（另开 PR）

1. 在子模块里检出新提交，`git add modules/caveman`，同步更新 `generate.py` 的 `PIN`。
2. 核对新提交的 `LICENSE`（如已是 Apache-2.0，按 AGENTS.md “Upstream file imports”确认可用，并更新 manifest 的 `upstream_license`）。
3. 对照上游 `skills/caveman/SKILL.md` 的变化决定是否调整 `overlay/` 的提示词；运行 `generate.py --check`。
4. 更新 manifest 与 SKILL.md frontmatter 的 SHA，经独立审查后提交。
