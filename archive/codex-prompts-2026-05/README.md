# 归档：原 `.codex/prompts/`（2026-05）

## 这是什么

14 个文件，全部产生于 2026-05-25 的一批 PR 审查（#434 / #435 / #436 / #439 / #440），
内容是当时写给 Argus 各条 finding 的回复稿、审计草稿，以及一个只有两个词的触发片段
（`argus-trigger-435.txt` 内容为 `/argus review`）。

它们**从未进入版本库**——直到本次归档为止一直是未跟踪状态。

## 为什么移出 `.codex/prompts/`

两个原因，任一都足够：

1. **那个位置的机制已废弃。** Codex CLI 不扫描项目级 `.codex/prompts/`，放在那里的文件
   不会被任何机制读取。留着只会让人误以为它是活的配置目录。
2. **内容本身是一次性的。** 这些是针对具体 PR 的具体 finding 写的回复，不是可复用的
   prompt 模板，也没有可以抽出来做成 skill 的方法论。

## 为什么保留而不是删除

其中几份（尤其 `reply-435-f1-adapter-loc.md`、`440-consolidated-disagree.md`）是
`mercury-gui/` 适配层 200 行规则豁免的原始 DISAGREE 措辞。该豁免的结论已经写进
`CLAUDE.md` 的 MUST 段与 `.mercury/docs/guides/cherry-pick-carve-out.md`，
这里留的是当时的原文，作为那条治理链的一手记录。

## 使用须知

**不要把这里的任何文件当模板复用。** 它们绑定 2026-05 当时的 PR 编号、commit SHA 与
代码状态，其中的行号引用与结论早已随代码变化而失效。需要 DISAGREE 的现行依据，
读 `.mercury/docs/guides/cherry-pick-carve-out.md`。

归档于 2026-08-14，Issue [#571](https://github.com/392fyc/Mercury/issues/571)（Codex 迁移 G3）。
