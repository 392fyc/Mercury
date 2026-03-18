# Git 分支规范 (GitHub Flow)

## 分支结构

| 分支 | 用途 | 合并权限 |
|------|------|---------|
| `master` | 稳定版 — 仅通过 PR 从 develop 合并 | Human / Main Agent |
| `develop` | 集成基线 | Main Agent (从 feature ff-merge) |
| `feature/TASK-XXX` | 单任务工作分支 | Dev 在此工作；Main 创建和合并 |

## 工作流

1. Main Agent 从 develop 创建 `feature/TASK-XXX`，checkout 后派发给 Dev
2. Dev Agent 在该分支工作，commit + push。**永不切换分支**
3. Main Review PASS → Main Agent merge feature → develop，push
4. Milestone release: PR develop → master

## Commit 规范

格式: `{type}({task_id}): {summary}`

type: feat / fix / refactor / chore / docs

## Dev Agent Git 权限

| 允许 | 禁止 |
|------|------|
| `git add`（scope 内文件） | `git switch` / `git checkout <branch>` |
| `git commit` | `git branch -d` / `git reset` / `git stash` |
| `git push origin <branch>` | `git rebase` / `git merge` |
| `git diff` / `git status` / `git log` | `git add -A` / `git add .` |
| | `git push --force` |
| | 操作 master / develop |
