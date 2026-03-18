# Dev Agent: TaskBundle 工作流

## 接收任务
1. 读取 TaskBundle JSON（通过 dispatch prompt 或 KB 路径）
2. 读取 `readScope.requiredDocs` 中列出的所有文档
3. 理解 `codeScope.include` 和 `allowedWriteScope`

## 写入边界
- **仅**在 `allowedWriteScope.codePaths` 内写入
- **仅**更新自己 TaskBundle 的 `implementationReceipt`
- **绝不**触碰 `docsMustNotTouch` 中的文件

## 完成提交
填写 `implementationReceipt`:
```json
{
  "implementer": "<agent-id> (<model>)",
  "branch": "<working branch>",
  "summary": "<what was done>",
  "changedFiles": ["..."],
  "evidence": ["<test output, runtime proof>"],
  "docsUpdated": ["..."],
  "scopeViolations": [],
  "completedAt": "<ISO timestamp>"
}
```

然后: git commit → git push → **停止**。

## 上报条件
- 需修改 scope 外文件
- TaskBundle 描述歧义
- 环境问题阻塞
- 需架构级变更

**禁止**静默扩大范围。**禁止**猜测设计意图。
