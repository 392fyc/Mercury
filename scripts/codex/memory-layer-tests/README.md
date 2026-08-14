# 记忆层验证脚本（用户级 hook）

Issue [#571](https://github.com/392fyc/Mercury/issues/571) / G5-3。

## 这些脚本验的是什么

记忆层的实现在**用户级** `~/.claude/hooks/` 与 `~/.claude/scripts/`，不在本仓。
按 `CLAUDE.md` 的「用户级变更治理」，那类改动必须在 Mercury 留下
「命令清单 + diff 摘要 + 验证步骤」的记录 —— 这三个脚本就是可重跑的验证步骤。

| 脚本 | 验什么 |
|---|---|
| `hook-stdin-smoke.py` | 三个 hook 在 **Codex 形态**的 stdin 下不崩、有产出 |
| `transcript-parser-verify.py` | 解析器能读 Codex rollout，且 Claude 路径未回归、不重复计数 |
| `flush-harness-verify.py` | `MERCURY_FLUSH_HARNESS` 分流、codex 调用参数、输出取法、错误路径 |

## 跑法

```bash
python scripts/codex/memory-layer-tests/hook-stdin-smoke.py
python scripts/codex/memory-layer-tests/transcript-parser-verify.py
python scripts/codex/memory-layer-tests/flush-harness-verify.py
```

用 `~/.claude/.venv` 里的解释器跑（脚本会自己定位）。三个都以退出码表示结果。

`hook-stdin-smoke.py` 与 `transcript-parser-verify.py` **依赖本机存在真实的 Codex
rollout 文件**（`~/.codex/sessions/`）。用真实数据而非手写夹具是刻意的：手写夹具的
形状可能本身就不对，那样测出来的「通过」和「失败」都不能说明真实行为 ——
本次就是先用合成夹具得出结论、再用 6 个真实 rollout 复核，才敢下判断。

## 为什么每条判据都在那里（都是踩出来的）

- **「exit 0 不等于干了活」**。三个 hook 最初都 exit 0，但 SessionEnd 一个产物都没有。
  日志里的 `SKIP: empty context` 才是真因：解析器读出零条消息。所以
  `hook-stdin-smoke.py` 除了退出码还看 stderr 与产出。
- **两层格式差异**。Codex rollout 每行只有 `timestamp`/`type`/`payload`，
  内容嵌在 `payload` 里；且 content 块的 type 是 `input_text`（OpenAI Responses 形态）
  而非 Claude 的 `text`。**只修其中一层仍然读到空。**
- **不能重复计数**。`event_msg/user_message` 与 `response_item`(role=user) 表示
  同一轮用户输入，两个都收会把每条用户消息记两遍。
- **codex 的 stdout 是事件流，不是回答**。`claude -p` 只打印回答，而 `codex exec`
  打印运行日志。照抄 `result.stdout` 会把一整片事件日志当成摘要写进记忆 ——
  坏得很像成功。必须用 `-o/--output-last-message`。变异验证过：把它改回读 stdout，
  测试立刻抓到 `[EVENT] task_started` 混进了摘要。
- **`--skip-git-repo-check` 是必需的**，因为 cwd 是 `~/.claude`，它不是 git 仓库。
- **查找顺序会骗过测试**。`_find_claude_exe()` 先看 `CLAUDE_CODE_EXECPATH`、
  再看 `~/.local/bin/`，最后才查 `PATH`。桩程序只放 `PATH` 里会被真的 claude.exe
  抢先命中 —— 第一版测试就这样测了个寂寞（返回真实的 `FLUSH_OK`）。

## 回滚

改动前的原件都留了备份：

```
~/.claude/hooks/session-end.py.backup-pre-571-g53
~/.claude/hooks/pre-compact.py.backup-pre-571-g53
~/.claude/scripts/flush.py.backup-pre-571-g53
```

`mv` 回去即可。
