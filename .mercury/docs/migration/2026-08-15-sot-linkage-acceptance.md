# TASK-571 四根联动验收

- 日期：2026-08-15
- 分支：`feature/TASK-571-linkage`
- 基线：`82ebafc1fdd21ce133cbd7e2749ac7b206379fd8`
- 证据根：`D:\Codex-Migration-Backup\2026-08-15-mercury-sot\sot-evidence`
- 结果：**PASS（有明确剩余风险）**

本记录只保存路径、计数、状态、哈希和裁决，不保存规则正文、笔记正文、令牌、静态认证头或原始 MCP 输出。

## 验收结果

| 范围 | 结果 | 关键证据 |
|---|---|---|
| SoT 字段权威 | PASS | 当前 Mercury 权威测试 17/17；设计库提交 `c49a8f947865109a28577a46e016181f381193d6` 与两份保护区 receipt 绑定。 |
| 规则 API / snapshot parity | PASS | 两侧均为 23 条，规范化 SHA-256 相同，差异数 0；证据绑定设计库验收记录及提交 receipt。 |
| Godot MCP | PASS | 使用一次性 Codex CLI 配置覆盖启动固定 `@satelliteoflove/godot-mcp@2.15.0`；真实 `project(action=get_info)` 返回预期项目名、路径和主场景。 |
| Obsidian MCP | PASS | 环境变量名认证；UUID 笔记创建、两次读取、本地 SHA-256、删除前保护检查、回收站删除和缺失确认通过。 |
| KB 本地 fallback | PASS | Obsidian MCP 配置临时禁用时，现有 `scripts/kb.py` 写入和读取成功；精确探针文件已清理，配置已恢复。 |

执行只读汇总命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/codex/mcp/verify-sot-linkage.ps1 `
  -EvidenceRoot 'D:\Codex-Migration-Backup\2026-08-15-mercury-sot\sot-evidence' `
  -ReadOnly
```

结果为五个区段全部 `PASS`，进程退出码 0。汇总脚本在解析前校验五份固定证据哈希，并对禁止凭据形态与各证据的脱敏标志执行失败关闭检查。

## 生命周期与 dirty 保护

- Godot 全局 MCP 条目保持不存在；仅专用一次性任务启用，避免多个 Codex 任务争用 6550。
- Godot 探针结束后，Godot MCP 子进程为 0，6550 建立连接为 0，Godot 编辑器 PID 22476 继续监听。
- Godot 仓原有 `project.godot` 状态哈希前后相同。
- Obsidian UUID 探针结束后，探针路径和本次创建的空父目录均不存在；KB 的 6 个既有插件 dirty、233 个文件清单和两项哈希均恢复到基线。
- KB fallback 探针路径不存在，KB Git 状态哈希前后相同。
- 设计库的 `.commits_list.txt` 保持未跟踪、未暂存；本阶段未写入设计库、Godot 仓或 KB 的持久内容。

## Codex 配置修正

- Obsidian 配置补齐 `required = false` 与 `default_tools_approval_mode = "writes"`；未写入静态令牌或认证头。
- `hooks.json` 删除 Codex 不支持的顶层 `$comment`，并把 `SessionEnd` 超时从 30 秒改为官方上限 3 秒。
- 以上是用户级配置修正，不进入 Mercury Git 提交；当前文件哈希已由脱敏证据绑定。

## 证据哈希

| 文件 | SHA-256 |
|---|---|
| `authority.json` | `7c87e64aa1a653b174dd03aee71c7c3ddf688d2f31c8190fa0614f50be8706a1` |
| `rules-parity.json` | `740baec26c39a576813b48e31510ac4fbd677da5670fd3883edbe55da3faeabd` |
| `godot-mcp.json` | `736ad5372e7659700eb2d0eae35e07ec7bf408e163ccea19517a4796d610499f` |
| `obsidian-mcp.json` | `eae5ed1a14fc973f5c58ff3ebdc984df82855ec55a6536cc916c16e5e0f6687e` |
| `kb-fallback.json` | `35e4562adaa0d33ffc4cb1d421aa245200cec10d2fabea49258eb6bed4dd1900` |
| `scripts/codex/mcp/verify-sot-linkage.ps1` | `e9b56c1a688e5f99dfae1110755eb1f3e759f25d77dbf1e3372c035be81583bf` |

## 剩余风险与后续边界

1. SoT 的五个项目 skills 仍缺少 Codex 原生合同测试和脱敏触发报告，部分文本仍引用旧 Claude/Obsidian 工具名。本阶段不伪造通过，记录为 `native rebuild remaining risk`。
2. 原计划中的 KB `current-session.md` 与用户后续裁决冲突；项目活跃记忆的权威落点是 `D:\Mercury\Mercury\.mercury\memory\`。因此不创建旧入口，相关 Task 3 视为被新裁决取代。
3. `hooks.json` 目前仍调用 `~/.claude/hooks` 下的共享脚本和 Python 环境。它现在可被 Codex 正确解析，但在退役 Claude 源之前仍需迁至 Codex/Mercury 原生路径。
4. Godot MCP 的传递依赖仍可能漂移；直接包版本已固定为 2.15.0。全局服务保持关闭是有意的运行约束。
5. 规则 parity 使用已绑定提交的既有只读记录，本次没有重新抓取规则正文；若规则源或 snapshot 提交变化，必须重新生成 parity 证据。
6. OMC 插件缓存版本为 4.15.10，但用户级 `AGENTS.md` 标记仍为 4.14.7；不影响本次联动，但属于后续配置漂移清理。

本验收不授权进入 C 盘清理、Claude/OMC 退役、GUI 删除或其他 legacy retirement 阶段。
