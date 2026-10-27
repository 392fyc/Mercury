# html-plan 适配层：上游来源

- 上游仓库：[anthropics/claude-plugins-community](https://github.com/anthropics/claude-plugins-community)，路径 `html-plan/`（插件作者 Thariq Shihipar）。
- 子模块：`sources/claude-plugins-community`，钉住提交 `88003be1129c09381d27100b37a8fdc21b3214bf`（2026-10-05，"html-plan: calmer page style"，也是 `html-plan/` 目前最新的一次改动）。
- 许可证：插件清单 `html-plan/.claude-plugin/plugin.json` 声明 `MIT`，插件目录没有单独的版权行或许可文件；仓库根 `LICENSE` 为 Apache-2.0。生成的 `LICENSE` 同时写明两点并附仓库 LICENSE 原文。
- 导入记录：Mercury Issue #655（导入）、#668（改为子模块与适配层）。逐文件条目见 `.mercury/state/upstream-manifest.json`。
- 输出位置：`packs/plan/skills/html-plan-zh/`（除 `claude-entry.md` 外全部由 `generate.py` 生成，不手改；`tools/mercury.py` 把整个 pack 装到项目的 `.mercury/packs/plan/`）。`claude-entry.md` 是不生成的薄入口，由 pack.json 声明为 Claude 专用入口，`tools/mercury.py` 据此生成 `mercury-html-plan-zh` 的 Claude 薄入口。

## 文件归类

| 输出文件 | 归类 | 来源 |
|---|---|---|
| `runtime/htmlplan.js`、`htmlplan.css`、`pack.mjs` | 机器转换 | 上游 `html-plan/skills/html-plan/runtime/` 同名文件，经 `localize.py` 按 `localize.table`（131 条精确替换，命中数不符即失败）转换并加署名头 |
| `references/blocks.md` | 补丁 | 上游同名文件 + `blocks.md.patch`（顶部加署名与中文说明，正文原样） |
| `LICENSE` | 拼接 | `license-header.txt` + 上游仓库根 `LICENSE` 原文 |
| `SKILL.md`、`examples/scheduled-send.html` | overlay（整篇翻译） | 上游同名文件的中文译本；整篇改写，补丁没有意义 |
| `UPSTREAM.md` | overlay（Mercury 自写） | 随技能下发的改动说明 |
| `references/design-proposals.md` | overlay（Mercury 自写，#661） | 上游没有对应文件，不登记 manifest |

`localize.py` 是适配层代码（原在技能目录 `maintenance/localize.py`），不属于下发内容，也不登记 manifest。

## 命令

```
python packs/plan/build/generate.py           # 写出
python packs/plan/build/generate.py --check   # 比对；有差异时退出 1 并列出文件
```

比对忽略 CRLF/LF 差异（Windows 的 `core.autocrlf` 会改工作区行尾）。

## 升级步骤（另开 PR）

1. 在子模块里检出新提交，`git add sources/claude-plugins-community`，同时把 `generate.py` 里的 `PIN` 改成新提交（否则 `--check` 只会报“未检出到钉住的提交”）。
2. 先核对上游 `html-plan/` 自钉住提交以来的差异，确认 `plugin.json` 与目录里没有新增 hooks、MCP、命令或可执行内容；确认 `plugin.json` 的许可证声明。
3. 运行 `generate.py --check`。`localize.table` 有失效项会逐条列出，按上游新文本修表；补丁不再适用时重做 `blocks.md.patch`。
4. 对照上游 diff 手动更新 `overlay/SKILL.md`、`overlay/examples/scheduled-send.html`（整篇翻译，不会自动更新）。
5. 更新所有写死旧提交或日期的出处：`blocks.md.patch` 第 1 行的 SHA、`localize.py` 署名头的 `DATE`、`overlay/SKILL.md` frontmatter 的 `upstream_sha`、`overlay/UPSTREAM.md`、`license-header.txt` 与 manifest 的 SHA；运行 `generate.py` 写出，经独立审查后提交。

## 原版插件的版本固定

见 `overlay/UPSTREAM.md` 末节（`.claude/settings.json` 对 `claude-community` 市场设置了 `autoUpdate: false`）。
