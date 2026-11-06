# Harness 报告

回答一个问题：Mercury 配置给 Agent 的入口（记忆、skill、知识库、规则文件、lane 消息），Agent 实际用了没有。结果用来决定 harness 哪里该改。它是原则 M1 的具名例外，范围见 [决策记录](../../docs/03-decisions/DEC-2026-11-05-harness-monitor.md)。

## 运行

```bash
python tools/harness_monitor/monitor.py
```

一次运行做三件事，结束后不留任何进程：

1. `agentsview sync` 把 Claude Code 和 Codex 的日志同步进它自己的 SQLite 副本。
2. 只读这份副本，用 `rules.py` 重新分类日志有变化的会话，把计数写入 `stats.db`。
3. 生成 `report.html`，用浏览器直接打开，不需要服务器。

`--no-sync` 跳过第 1 步。同步或统计失败时仍生成报告，页面顶部会写明，数字保留上次的结果，程序以退出码 1 结束。

想定期更新时，用系统的计划任务运行这条命令。这属于用户级配置，本工具不替你安装。

## 本机设置

状态都在 `.mercury/state/harness-monitor/`，已被 Git 忽略。`config.json` 可写：

| 字段 | 作用 |
|---|---|
| `agentsview_bin` | agentsview 程序路径，也可用环境变量 `MERCURY_AGENTSVIEW_BIN` |
| `agentsview_data_dir` | agentsview 数据目录，默认在状态目录下 |
| `exclude_projects` | 不统计的项目名，项目名或工作目录含这些文字的会话都跳过 |
| `exclude_text` | 正则；调用内容匹配时跳过这一条 |
| `project_aliases` | 把 agentsview 给出的项目名合并，例如 `{"first_pass_approve": "Argus"}` |

私人项目只写在这里，不写进仓库。

## agentsview

钉住 0.46.1（[kenn-io/agentsview](https://github.com/kenn-io/agentsview)，MIT），程序会校验版本和数据库字段，不符时不更新统计，并在报告里写明。Windows 版 `agentsview_0.46.1_windows_amd64.zip` 的 SHA256 是 `e1d0b5112172f29c8d1182e51927c3aeef07247c54a154f163a33d0c719e0c97`。

- 它的数据库是完整的对话副本，约 1.4 GB，必须留在本机。`stats.db` 和 `report.html` 只含计数和工具、skill、项目的名称；会话只留下哈希，不存会话 ID。
- 运行时已关闭遥测和更新检查。
- 这里只用一次性同步。常驻服务（`agentsview serve`）会定期拉取公开价格表，本工具不启动它。

## 怎么读报告

- **先选主会话还是子代理**：两者不能混算。子代理按主会话写的提示词工作，数量又远多于主会话，混在一起时占比主要在描述子代理，所以报告没有“全部”这个选项。
- **会话占比**最有用：比如某项目只有 5% 的主会话读过项目记忆，说明这个入口可能没起作用。
- **项目记忆和宿主自带记忆分开看**：项目记忆是 Mercury 配置的 `.mercury/memory`、`.sot/memory`；宿主自带记忆是 Codex 自己的 `.codex/memories`。记忆目录下 `evidence` 里的文件是任务工作文件，两类都不算。
- **看日志范围**：报告写明每个宿主的日志起止日期。两边范围不同时，比较要打折扣。
- 这是调用次数，不说明内容有没有被用上，也不说明是否做了验证。
- 项目之间、宿主之间的会话数量和工作方式不同，比较时要看占比，并结合具体情况。
- 标“未验证”的格子不要用来下结论。原因写在报告的“说明”一列。
- Skill 名称表没有单独验证，只作线索；命令文本里提到的路径也可能被算进去。

## 改规则

`rules.py` 和 `monitor.py` 里的 `TOOLS` 共同决定计数的含义，`validation.json` 记录规则哈希、工具清单和每格结论。改动其中任何一个，旧的验证就失效：测试会失败，报告也会把所有格子标成未验证。`monitor.py` 改动后，下次运行会全量重算，旧版的 `stats.db` 也会自动重建。要先按 [验证记录](../../docs/04-research/mercury/harness-monitor-validation-2026-11.md) 的方法用新样本重新验证，再更新 `validation.json`。

出现新工具或模型习惯变化时，报告的“规则之外”会列出新工具，但已有类别悄悄变差是看不出来的，所以需要隔一段时间重新抽检。

## 拆除

没有其他代码依赖它。需要删除：

- `tools/harness_monitor/`、`tests/tools/test_harness_monitor.py` 和本机状态目录。
- 决策记录和验证记录，以及 `docs/03-decisions/README.md`、`docs/04-research/mercury/README.md` 里对应的索引行。
- `docs/01-rules/principles.md` 的 M4 和 M1 的括注。
- `docs/02-guides/architecture.md` 里的提及，以及 `docs/00-progress/README.md` 里 #755 那一行（如还在）。
