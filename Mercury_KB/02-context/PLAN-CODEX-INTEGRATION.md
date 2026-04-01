# Codex Sub-Agent Integration Plan (Issue #117)

**目标**: 通过 codex-plugin-cc 将 Codex 注入 Research Agent 作为内部 sub-agent，分担 token 密集型文件扫描，保护主 session token 预算。

**状态**: 设计中 — 细节待 Phase A 技术验证后推进

---

## 架构概览

```
Research Agent (Claude Sonnet, CC session)
  │
  ├─ 正常流程: WebSearch + WebFetch + Grep (synthesis)
  │
  └─ token 压力时 (budget < 40K 或 >5 连续 Grep):
       ↓ Agent tool { subagent_type: "codex:codex-rescue" }
       Codex sub-agent (独立 200K context，不消耗主 session)
         → 文件扫描 / 大范围 Grep / 代码分析
         → 返回精炼 findings
       Claude 主 session: 综合 + KB 写入
```

**关键前提**: codex-plugin-cc 是官方插件，通过本地 Codex CLI 运行，
无 MCP/RPC 协议开销，sub-agent 独立 context，结果以单条消息返回父 session。

---

## 实施阶段

### Phase B: Research Prompt 委托指令（即时，无依赖）

**文件**: `packages/orchestrator/src/task-manager.ts` → `buildResearchPrompt()`

在 tokenBudgetHint 注入段之后添加 Codex 委托规则：

```
## Codex Sub-Agent Delegation

When token budget < 40,000 OR task requires 5+ consecutive file scans:
1. Delegate file-scanning to Codex via Agent tool { subagent_type: "codex:codex-rescue" }
2. Use Codex findings as raw data — you synthesize and write KB
3. Never run 5+ Grep calls in sequence without considering Codex delegation
```

Phase A 完成前 prompt 先写好（不影响现有流程）。

---

### Phase A: 安装 codex-plugin-cc + Research Session 配置

**必须在实施前 WebFetch 验证的细节**:

1. **安装命令** — 从官方仓库获取准确步骤
2. **Research session 插件可用性** — ClaudeCodeAdapter 能否在 session 启动时
   指定插件配置，或必须全局启用
3. **`codex:codex-rescue` sub-agent 接口** — prompt 参数格式确认
4. **sub-agent token 独立性** — 确认子 agent context 不计入主 session

**实施步骤**（验证后执行）:
- [ ] 安装 codex-plugin-cc
- [ ] 测试 rescue subagent 在当前会话可用
- [ ] 确认 research sessions 能调用该 subagent type
- [ ] 必要时在 startRoleSession() 注入插件配置

---

### Phase C: Codex Deep-Research 协议注入

**前置条件**: Phase A 完成

在 Research Agent 调用 Codex sub-agent 的 prompt 模板中注入研究质量协议：

```
[Codex Sub-Task Research Protocol]
- EVIDENCE OVER CLAIMS: only report what you find, not assumptions
- ITERATIVE SEARCH: try multiple patterns before concluding "not found"
- OUTPUT FORMAT: structured list with file:line references
- SCOPE: stay within specified files/directories only
```

不修改 Codex 本身，通过 Agent tool prompt 参数注入。

---

## 技术风险

| 风险 | 可能性 | 缓解 |
|------|--------|------|
| 安装方式与预期不符 | 中 | Phase A 前 WebFetch 官方 README |
| Research session 无法动态启用插件 | 中 | 全局启用作为 fallback |
| sub-agent token 计入主 session | 低 | 官方文档明确子 agent 独立 context |

---

## 依赖链

```
Issue #109 (loop+dispatch) ✓ MERGED
  ↓
#117 Phase B → #117 Phase A → #117 Phase C
  ↓
Issue #101 Phase 3 研究任务（OTel + middleware + wave scheduling）
```

*Created: 2026-04-02 | Issue #117*
