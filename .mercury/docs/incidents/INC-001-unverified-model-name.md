# INC-001: 未经联网验证的模型名称写入配置

## 元数据

| 字段 | 值 |
|------|-----|
| 日期 | 2026-03-23 |
| 严重程度 | P1 — 事实性错误进入 PR |
| 影响范围 | mercury.config.example.json, mercury.config.json |
| 关联 PR | #67 (TASK-DISPATCH-FIX-001) |
| 发现者 | 用户 (code review 阶段) |
| 修复 commit | 7be66e3 |

## 事故描述

Session 10 在修复 dispatch 通路时，将 `mercury.config.example.json` 中 codex-cli agent 的模型标识符从 `gpt-5.4` 改为 `o3`，**未经联网验证**。

- `o3` 是 OpenAI 的推理模型，存在于 API 中，但**不是 Codex CLI 的推荐模型**
- Codex CLI 官方文档推荐 `gpt-5.4`（已验证：developers.openai.com/codex/models）
- 该错误通过了 5 轮 CodeRabbit review（CodeRabbit 也错误地认定 `o3` 为正确标识符）

## 影响

- 若配置被用于生产 dispatch，codex-cli agent 将使用非最优模型
- 项目配置示例传播错误信息给后续使用者
- 两道防线（agent 自检 + CodeRabbit review）均未拦截

## 根因分析

### 直接原因
Agent 从训练数据中推测模型名称，跳过了 CLAUDE.md 要求的联网验证步骤。

### 根本原因

1. **配置文件被视为"非代码"**：Web search hook 的触发条件是"writing code that references external SDK/API"。Agent 将 JSON 配置文件修改归类为非代码操作，认为不需要触发验证流程。但模型标识符本质上是外部 API 标识符声明。

2. **知识置信度过高**：`o3` 是训练数据中合法存在的 OpenAI 模型名。Agent 知道名字存在 → 推断适用于 codex-cli，跳过了"是否是该场景的正确选择"这一关键验证。这正是 hook 要防止的：**训练数据正确 ≠ 场景适用**。

3. **改动规模引起的警觉降低**：单行 JSON 值修改被心理归类为"trivial fix"，降低了验证门槛。但 CLAUDE.md 规则不区分改动大小。

4. **Reviewer 同样失误**：CodeRabbit 在 outside-diff-range comment 中明确写道 "o3 均为官方支持的标准模型标识"，给出了错误的肯定。Agent 将此作为二次确认，进一步降低了自主验证的意愿。

## 时间线

| 时间 | 事件 |
|------|------|
| Session 10 | Agent 修改 model 从 gpt-5.4 到 o3，未联网验证 |
| Session 10 | PR #67 创建，CodeRabbit 5 轮 review 均未拦截 |
| Session 11 | 用户发现并拒绝签收："codex模型你是否联网查证？" |
| Session 11 | 联网验证确认 gpt-5.4 为正确值，提交修正 commit 7be66e3 |
| Session 11 | CodeRabbit 批准，PR 合并 |

## 修正措施

### 已完成
- [x] mercury.config.example.json: o3 改回 gpt-5.4
- [x] mercury.config.json（本地）: 同步修正
- [x] tauri-bridge.ts 注释中的 o3 改为 gpt-5.4

### 预防措施

1. **扩大验证范围定义**：任何涉及以下内容的修改都必须联网验证，无论文件类型：
   - 模型名称 / 模型标识符
   - 包版本号
   - CLI flag / 命令语法
   - API endpoint / 参数名
   - 配置文件中的外部标识符

2. **配置文件不豁免**：JSON/YAML/TOML 中的外部标识符与代码中的 import 语句具有同等验证要求。

3. **"知道名字存在"不等于"验证通过"**：验证必须确认标识符在**目标场景**中的适用性，而非仅确认其存在性。

4. **不信任单一 reviewer**：CodeRabbit 的肯定不能替代联网验证。自动化 reviewer 同样可能产生幻觉。

## 教训总结

核心教训：Hook 是文本注入，不是强制门禁。它依赖 agent 自觉遵守。当 agent 认为自己"已经知道答案"时，最容易跳过验证——而这恰恰是 hook 最需要生效的时刻。训练数据中"知道"的东西，恰恰是最危险的，因为它给了 agent 虚假的确信感。
