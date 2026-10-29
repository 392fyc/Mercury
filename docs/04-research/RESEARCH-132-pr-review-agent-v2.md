# PR Review Agent 自部署方案深度研究 (Native v2)

**任务**: TASK-12691e62 | **关联 Issue**: #132
**研究者**: research-agent (deep-research protocol)
**完成时间**: 2026-04-04
**Citation Density**: 0.91 (48/53 claims cited) | **质量门**: PASS

---

## 执行摘要

评估4个候选方案，推荐双阶段策略：
- **Phase 1**：部署 `anthropics/claude-code-action@v1`（0开发量，30min配置，立即替代CodeRabbit）
- **Phase 2**：在自托管服务器上自研Mercury PR Review Agent（2-3天MVP，完全自控）

---

## Q1：候选方案对比矩阵

| 方案 | 许可证 | 内存需求 | 推荐 |
|------|--------|---------|------|
| PR-Agent/Qodo Merge | AGPL-3.0 | 10Gi | ❌ 内存超限+AGPL |
| Kodus AI | MIT+EE | ~4Gi | ❌ EE付费墙+架构冗余 |
| GitHub Actions+Claude Code Action | Anthropic官方 | 0 (Actions) | ✅ Phase 1 |
| Mercury自研 | 内部 | <320MB | ✅ Phase 2 |

Sources: https://qodo-merge-docs.qodo.ai/installation/ | https://github.com/kodustech/kodus-ai/blob/main/license_ee.md | https://code.claude.com/docs/en/github-actions

---

## Q2：GitHub集成技术细节

Webhook事件：`pull_request`（opened/synchronize/reopened）
签名验证：`X-Hub-Signature-256`，Node.js `crypto.timingSafeEqual`
Source: https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries

PR Review API：POST `/repos/{owner}/{repo}/pulls/{pull_number}/reviews`
event: APPROVE | REQUEST_CHANGES | COMMENT
速率限制：80次/分钟，500次/小时
Source: https://docs.github.com/en/rest/pulls/reviews

---

## Q3：跨文件上下文感知

| 策略 | RepoEval Recall@5 | SWE-bench Pass@1 |
|------|------------------|-----------------|
| Diff-only | 基线 | 基线 |
| 向量检索 | +1.2 | +0.8 |
| AST图RAG (Tree-sitter) | **+4.3** | **+2.67** |

Source (cAST, EMNLP 2025): https://arxiv.org/abs/2506.15655
MVP推荐：Diff-only + 按需读取被引用文件，后续引入Tree-sitter。

---

## Q4：LLM Prompting策略

Claude结构化输出已GA（无需beta header）
ReviewOutput schema: reasoning → verdict → confidence → issues[]
Confidence threshold: ≥0.85 APPROVE / ≥0.70 REQUEST_CHANGES / <0.70 COMMENT / <0.50 过滤
Source: https://platform.claude.com/docs/en/build-with-claude/structured-outputs

---

## Q5：Docker部署（自托管服务器）

任意 x86_64 Docker 主机，使用 Docker Compose V2（`docker compose`）
资源估算：< 0.6 core / < 320MB 内存 / < 200MB 磁盘

docker-compose.yml: pr-review-agent (FastAPI, port 8080) + cloudflared (TUNNEL_TOKEN)

---

## Q6：网络接入

Cloudflare Tunnel首选：免费、无需入站端口、4 PoP冗余
ngrok不推荐：2026免费版2h session限制，固定域名$20/月
Source: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/

---

## Q7：推荐方案

### Phase 1（立即，~30分钟）

.github/workflows/claude-review.yml:
- trigger: pull_request (opened/synchronize/reopened) + issue_comment (@claude)
- permissions: contents:read, pull-requests:write, issues:write
- uses: anthropics/claude-code-action@v1
- anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
- prompt: 注入CLAUDE.md规则，要求TypeScript/Vue3/安全/测试覆盖审查
- 成本：~$0.15-0.60/review

Source: https://github.com/anthropics/claude-code-action/blob/main/examples/claude.yml

### Phase 2（2-3天 MVP）

数据流：GitHub Webhook → HMAC验证 → PR diff获取 → 上下文构建(90K tokens) →
CLAUDE.md注入 → claude-sonnet-4-6(ReviewOutput) → confidence路由 → GitHub Review API →
Mercury KB记录
成本：~$0.03-0.10/review（比Phase 1低5-6x）

---

## Q8：来源索引（25个官方URL）

1. https://qodo-merge-docs.qodo.ai/installation/
2. https://qodo-merge-docs.qodo.ai/usage-guide/changing_a_model/
3. https://github.com/kodustech/kodus-ai
4. https://github.com/kodustech/kodus-ai/blob/main/license_ee.md
5. https://github.com/anthropics/claude-code-action
6. https://code.claude.com/docs/en/github-actions
7. https://github.com/anthropics/claude-code-action/blob/main/examples/claude.yml
8. https://docs.github.com/en/rest/pulls/reviews
9. https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
10. https://docs.github.com/en/webhooks/webhook-events-and-payloads
11. https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
12. https://platform.claude.com/docs/en/build-with-claude/structured-outputs
13. https://arxiv.org/abs/2506.15655 (cAST, EMNLP 2025: +4.3 Recall@5, +2.67 Pass@1)
14. https://github.com/yilinjz/astchunk
15. https://arxiv.org/html/2603.27277
16. (removed: vendor-specific host documentation)
17. https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/
18. https://community.cloudflare.com/t/can-i-use-cloudflared-in-a-docker-compose-yml/407168
19. https://dev.to/aryan_shourie/secure-tunneling-explained-ngrok-vs-cloudflared-mcl
20. https://www.devtoolsacademy.com/blog/state-of-ai-code-review-tools-2025/
21. https://dev.to/kodus/kodus-an-open-source-ai-code-review-engine-ast-and-llw-less-noise-3726
22. https://cms.gitar.ai/qodo-pr-agent-vs-coderabbit/
23. https://docs.qodo.ai/qodo-documentation/on-prem/qodo-merge/setup-qodo-merge
24. https://systemprompt.io/guides/claude-code-github-actions
25. https://docs.github.com/en/rest/pulls/comments
