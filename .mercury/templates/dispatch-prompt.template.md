# Dispatch: {{taskId}}

{{context}}

**TaskBundle**: `{{taskFilePath}}`
**允许写入**: {{allowedWriteScope}}
**禁止修改**: {{docsMustNotTouch}}

## Task Bundle (machine-readable)

```json
{{bundleJson}}
```

## 执行协议

1. 读取 TaskBundle + readScope 全部文档
2. 实现 → 自动 verify (lint + test + scope-check)
3. 通过 → 填写 receipt 并 commit + push
4. 失败 → 自修复 1 次 → 仍失败则 STOP 并报告

## 歧义升级规则

遇到以下情况，**立即停止实现并升级到 Main Agent**:
- definitionOfDone 含主观判断项或无法验证的条件
- codeScope/allowedWriteScope 与实际代码结构不匹配
- 需要的 API/SDK 文档未在 readScope 中列出
- 任务描述存在多种合理解读
- 需要修改 scope 外文件才能完成任务

**禁止**基于猜测继续实现。升级时说明歧义点和可选方案。

## 完成指令

When complete, output a JSON receipt as your final message:

```json
{{receiptTemplate}}
```
