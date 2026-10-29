# RESEARCH-132: Self-Deployed PR Code Review Agent

**Issue**: #132
**Task**: TASK-9f5fed83
**Researcher**: claude-sonnet-4-6 (Research Agent)
**Date**: 2026-04-03
**Status**: Complete

---


## 1. Candidate Solutions Comparison Matrix

| Criterion | pr-agent / Qodo Merge | kodus-ai | GitHub Actions + Claude API | Mercury Self-Build |
|-----------|----------------------|----------|----------------------------|-------------------|
| License | AGPL-3.0 | MIT core plus Enterprise EE | N/A | Custom (internal) |
| Claude support | Yes (via LiteLLM) | Yes (model-agnostic) | Yes (native) | Yes (existing API key) |
| Self-hosting | Yes (Docker, GitHub App, Lambda) | Yes | N/A (GitHub runners) | Yes (self-hosted server) |
| Infrastructure | Webhook server or Lambda | Webhook server | None (GitHub Actions) | FastAPI plus Docker |
| Mercury integration | None | None | Partial (CLAUDE.md) | Full (role system, KB) |
| Setup complexity | Medium | Medium-High | Low (~30 minutes) | High (2-3 days MVP) |
| Rate limiting risk | Low (per-PR trigger) | Low | Low | Controlled |
| Private repo compliance | AGPL: internal use OK if unmodified | EE license for enterprise features | Clean | Clean |

### 1.1 pr-agent / Qodo Merge

License: AGPL-3.0. For self-hosting on internal infrastructure with no distribution to external users, AGPL compliance is met by using the software as-is. Modifying and serving over a network requires publishing source changes.

Claude backend: Fully supported via LiteLLM integration. Set model to the desired Anthropic model identifier and provide the Anthropic API key in the secrets configuration file. No OpenAI key required. Extended thinking is supported via a dedicated flag with configurable token budget parameters.

Self-hosting options: GitHub Action (Docker image pulled per workflow run), GitHub App (webhook-driven persistent server), AWS Lambda (stateless per-invocation), or bare Docker deployment.

Official docs: https://qodo-merge-docs.qodo.ai/usage-guide/changing_a_model/ and https://qodo-merge-docs.qodo.ai/installation/github/

### 1.2 kodus-ai

Architecture: TypeScript monorepo. Hybrid AST plus LLM approach where AST analysis reduces noise and hallucinations compared to pure-LLM review. Integrates Model Context Protocol servers for extended tool access. Model-agnostic (Claude, GPT, Gemini, Llama via Ollama, any OpenAI-compatible endpoint).

License: Dual licensing. MIT for core code. Enterprise Code (files with .ee. in filename or in ee/ directories) requires a paid Kodus Enterprise License. Production and internal deployment of Enterprise Code requires a valid license; evaluation-only local use is permitted without a license. Source: https://github.com/kodustech/kodus-ai/blob/main/license_ee.md

Activity: Latest release 2.0.22 published March 2026. Actively maintained with 976 stars, 89 forks, and 129 releases.

Concern: Enterprise feature paywall for production-grade review capabilities. Overlaps significantly with Mercury existing orchestration capabilities.

Official docs: https://kodus.io/en/ and https://github.com/kodustech/kodus-ai

### 1.3 GitHub Actions + Claude API (Recommended Phase 1)

Anthropic official claude-code-action GitHub Action runs Claude Code within GitHub Actions runners. Trigger on pull_request events with types opened, synchronize, and reopened. Permissions needed: contents read, pull-requests write, issues write. Pass anthropic_api_key from repository secrets.

Key advantage: No self-hosted infrastructure, no webhook exposure, respects CLAUDE.md project rules automatically, uses existing ANTHROPIC_API_KEY secret. Zero new operational surface area.

Cost: approximately 2-10 minutes per review (GitHub Actions minutes) plus 50K-200K tokens per review (Claude API).

Official docs: https://code.claude.com/docs/en/github-actions and https://github.com/anthropics/claude-code-action

### 1.4 Mercury Self-Build (code-review role, Recommended Phase 2)

Architecture: Extend the existing Mercury orchestrator with a code-review role. Mercury already has role-loader.ts (loads role YAML at runtime), role-prompt-builder.ts (builds system prompts), orchestrator.ts (dispatches agents), agent registry, task persistence, and MCP server support.

MVP components: FastAPI webhook listener Docker container on a self-hosted server, Cloudflare Tunnel for stable public webhook URL, .mercury/roles/code-review.yaml role definition file, webhook handler dispatching to orchestrator which calls Claude API and POSTs the review to GitHub.

Advantage: Full integration with Mercury KB, role system, and task tracking. No external service dependencies beyond existing Claude API access.

File references: packages/orchestrator/src/orchestrator.ts and packages/orchestrator/src/role-loader.ts in the Mercury repo.

---

## 2. GitHub Integration Technical Details

### 2.1 Webhook Payload (pull_request event)

Subscribe to event types: opened, synchronize, reopened.

Key payload fields: action (opened/synchronize/reopened), pull_request.number (integer PR number), pull_request.head.sha (SHA of the head commit), pull_request.head.ref (source branch name), pull_request.base.ref (target branch name), pull_request.diff_url (URL to fetch the unified diff), repository.full_name (owner/repo string).

Official docs: https://docs.github.com/en/webhooks/webhook-events-and-payloads

### 2.2 HMAC-SHA256 Signature Verification

Header: X-Hub-Signature-256, value formatted as "sha256=" followed by the hex digest.

Algorithm: HMAC-SHA256 with the webhook secret as the key and the raw request body as the message.

Node.js implementation: use createHmac from node:crypto with sha256, update with raw payload buffer, produce hex digest, then compare using timingSafeEqual from node:crypto. Never use plain equality operators for comparison since timing-safe comparison is required to prevent timing attacks against the signature verification.

Official test vector from GitHub docs: expected signature sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17 (see https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries for full test inputs).

### 2.3 POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews

Endpoint: POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews

Required headers: Authorization with Bearer token, Accept set to application/vnd.github+json.

Request body fields: commit_id (string, optional, defaults to most recent commit), body (string, required for REQUEST_CHANGES and COMMENT events), event (string: APPROVE, REQUEST_CHANGES, or COMMENT; omit to create PENDING draft), comments (array of inline review comments, each requiring path, line, side RIGHT for additions, and body).

Event semantics: APPROVE approves the PR and allows merging (body optional), REQUEST_CHANGES blocks merging until changes are made and requires body, COMMENT is non-blocking and requires body.

Response codes: 200 OK, 403 Forbidden, 422 Validation failed or endpoint spam detected.

Official docs: https://docs.github.com/en/rest/pulls/reviews

### 2.4 Authentication: GitHub App versus Fine-Grained PAT

GitHub App: 5000 requests per hour (15000 for Enterprise Cloud), per-installation repo-specific scope, short-lived tokens rotated automatically, recommended for production or multi-repo deployments.

Fine-grained PAT: 5000 requests per hour, per-repo scope, simple to create, sufficient for single-repo Mercury automation.

Required permission for both: Repository - Pull Requests - Read and Write.

Content creation rate limits per official GitHub docs: maximum 80 content-generating requests per minute, maximum 500 per hour. Maximum 900 REST API points per minute; POST review costs approximately 5 points.

Official docs: https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api

---

## 3. Cross-File Context Awareness

### 3.1 AST-Based Chunking versus Vector Retrieval

cAST research published in EMNLP 2025 (arxiv identifier 2506.15655): AST-based structural chunking outperforms line-based heuristics with +4.3 Recall@5 on RepoEval retrieval benchmark and +2.67 Pass@1 on SWE-bench code generation. The ASTChunk open-source toolkit implements this approach.

Key design principles: syntactic integrity (chunk boundaries align with complete AST nodes such as functions and classes), high information density (chunks packed up to a fixed token budget), language invariance (uses Tree-sitter grammar definitions with no language-specific heuristics), and reproducibility (concatenating all chunks reconstructs the original file verbatim).

Tree-sitter supports TypeScript, TSX, JavaScript, JSX, Python, and over 40 languages. Extracts function signatures, import graphs, type definitions, and class hierarchies.

Comparison: embedding-based retrieval excels at semantic similarity queries while structural AST retrieval excels at relational queries such as finding all callers of a changed function. Hybrid approaches combining both outperform either alone.

Paper: https://arxiv.org/abs/2506.15655 and toolkit: https://github.com/yilinjz/astchunk

### 3.2 Recommended Context Strategy for PR Review

Priority order from highest to lowest for a 128K context window:
1. PR diff (mandatory): fetch via the GitHub PR files endpoint; includes patch hunks per file
2. Full content of each changed file: complete context around the diff hunks
3. Direct callers and importers of changed functions: Tree-sitter import graph traversal, 2-hop maximum
4. Type definitions imported by changed files
5. Test files for changed modules
6. CLAUDE.md project rules: always include regardless of remaining budget
7. Vector-retrieved similar code patterns: include only if budget remains after items 1 through 6

Token budget management: reserve approximately 30K tokens for system prompt plus review output; allocate remaining approximately 90K for context. For PRs with more than 100 changed files, prioritize by module criticality.

### 3.3 Why Diff-Only Is Insufficient

Diff-only review misses breaking interface changes where callers are not in the diff, type compatibility issues for types imported but not modified, test coverage gaps for new code paths, and cross-module consistency violations. Always include direct callers plus type imports using Tree-sitter to build the dependency graph.

---

## 4. LLM Prompting Strategy

### 4.1 Pydantic Structured Output Model

Define a ReviewOutput Pydantic model with the following fields:
- reasoning (str): Chain-of-thought analysis field. The model must analyze correctness, types, security, test coverage, and CLAUDE.md rules before committing to a verdict. This field structure prevents premature conclusions.
- verdict: A Literal type with allowed values APPROVE, REQUEST_CHANGES, and COMMENT.
- confidence: A float in the range 0.0 to 1.0 representing the model confidence in the verdict.
- summary (str): Summary paragraph used as the main review body text posted to GitHub.
- line_comments: A list of LineComment items, each containing path (relative file path), line (integer), body (comment text), and severity (one of blocking, suggestion, or nit).

Pydantic AI supports three output modes per official documentation at https://ai.pydantic.dev/output/: Tool Output (default, most compatible with all models including Claude), Native Output (uses model native structured output API), and Prompted Output (schema injected into system prompt as fallback). Tool Output mode is recommended for broadest compatibility.

Official docs: https://ai.pydantic.dev/output/ and https://pydantic.dev/articles/llm-intro

### 4.2 Confidence Thresholds

- Confidence at or above 0.85: APPROVE (confident that no blocking issues exist)
- Confidence at or above 0.70 with blocking issues identified: REQUEST_CHANGES
- Confidence below 0.70 or ambiguous situation: COMMENT (non-blocking, prompts human reviewer)

### 4.3 System Prompt Design

Instruct the agent to review for: TypeScript type correctness and strict mode compliance, Vue 3 Composition API patterns and reactivity correctness, security issues such as injection vulnerabilities or accidental secret exposure, test coverage for new code paths, adherence to Mercury CLAUDE.md rules (inject relevant rule sections directly into the prompt), and scope creep meaning changes outside the PR stated purpose.

Critical instruction: write the full analysis in the reasoning field first, then set verdict and confidence. Only output APPROVE if confidence is at or above 0.85 with no blocking issues. For REQUEST_CHANGES, every line_comment must be actionable and reference a specific line.

### 4.4 Few-Shot versus Rule-Based Prompting

For project-specific TypeScript and Vue rules: rule-based prompting by injecting CLAUDE.md sections directly is preferred because the rule set is deterministic and auditable. Few-shot examples are better suited for nuanced style judgment where precise rules are difficult to articulate explicitly.

---

## 5. Docker Deployment on a Self-Hosted Server

### 5.1 Host Compatibility

Any x86_64 Docker host with Docker Compose V2 works; use the `docker compose` subcommand and Compose V2 YAML.

### 5.2 docker-compose.yml Structure for PR Review Agent

Two services needed: webhook-receiver (FastAPI application) and cloudflared (Cloudflare Tunnel client).

webhook-receiver service: build from local Dockerfile, restart policy of unless-stopped, expose port 8080, environment variables ANTHROPIC_API_KEY and GITHUB_WEBHOOK_SECRET and GITHUB_TOKEN sourced from an .env file, mount a named volume for AST cache persistence, healthcheck hitting GET /health every 30 seconds with 3 retries and a 10-second start period.

cloudflared service: use the official cloudflare/cloudflared image, restart policy of unless-stopped, command "tunnel --no-autoupdate run", TUNNEL_TOKEN environment variable obtained from the Cloudflare Zero Trust dashboard when creating the named tunnel.

Define a named volume with the local driver for persistent storage of Tree-sitter parse results between container restarts.

Reference: https://community.cloudflare.com/t/can-i-use-cloudflared-in-a-docker-compose-yml/407168

### 5.3 Dockerfile for Minimal FastAPI Webhook Receiver

Use python:3.12-slim-bookworm as the base image (approximately 150 MB versus approximately 1 GB for the full python image). Copy requirements.txt first to leverage Docker layer caching, install dependencies with pip install --no-cache-dir, then copy application code last since it changes most frequently. Use single-process startup: fastapi run app/main.py --port 8080.

Single process per container is the pattern recommended by FastAPI official documentation for containerized deployments where the container orchestrator manages replication and restarts.

Official docs: https://fastapi.tiangolo.com/deployment/docker/

### 5.4 Persistent Storage

Mount a host shared folder as a named Docker volume for AST cache (Tree-sitter parsed results) and optional review history log. A SQLite file in the mounted volume is sufficient for MVP-level review history. No separate database container is required.

---

## 6. Network Access for GitHub Webhook Delivery

### 6.1 Comparison Matrix

| Solution | Cost | Persistent URL | Reliability | Recommended |
|----------|------|---------------|-------------|-------------|
| Cloudflare Tunnel | Free | Yes (custom domain, no expiry) | High (4 edge PoP connections) | Yes |
| ngrok (2026) | Free: 2-hour sessions, random URL; $20/month for stable URL | Paid only | High | No |
| Port forwarding | Free | Requires static IP or DDNS | Dynamic IP risk | No |
| Tailscale plus Actions proxy | Free up to 100 devices | N/A (private network only) | High | Only if already deployed |

### 6.2 Cloudflare Tunnel (Recommended)

How it works: the cloudflared container makes outbound connections to Cloudflare global edge network (four concurrent connections to different data centers). The server never accepts inbound traffic directly. GitHub webhook HTTP POST events are sent to the webhook URL, routed through Cloudflare edge, then forwarded by cloudflared to the local FastAPI receiver on port 8080.

Setup steps:
1. Create a free Cloudflare account
2. Add your domain to Cloudflare DNS (or use a Cloudflare-provided subdomain for testing)
3. Create a named tunnel in the Cloudflare Zero Trust dashboard
4. Configure a DNS route mapping the tunnel to your chosen hostname
5. Copy the tunnel token from the dashboard and set it as TUNNEL_TOKEN in the Docker Compose .env file
6. Register the GitHub webhook URL with pull_request event subscriptions

Key advantages: unlimited bandwidth with no monthly cap, no session time limits (the tunnel persists indefinitely), URL never changes (stable webhook endpoint requires no reconfiguration in GitHub settings), no inbound firewall rules required on the self-hosted server, and redundant connections via four Cloudflare edge Points of Presence.

Official docs: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/

### 6.3 ngrok Status (2026)

As of February 2026, ngrok eliminated persistent URLs and added two-hour maximum session limits on the free tier. Paid plans start at $20 per month for one custom domain. Not recommended when Cloudflare Tunnel provides a superior free alternative.

Reference: https://dev.to/aryan_shourie/secure-tunneling-explained-ngrok-vs-cloudflared-mcl

---

## 7. Recommended Solution and MVP Architecture

### 7.1 Decision

Phase 1 (Immediate, approximately 30 minutes): Deploy anthropics/claude-code-action via GitHub Actions. Zero new infrastructure. Directly resolves CodeRabbit slow response and rate-limit problems by eliminating dependence on a third-party service. Respects CLAUDE.md project rules automatically. Constraint: no persistent cross-PR memory and no AST-level context beyond what Claude Code reads from the checked-out repository during the Actions run.

Phase 2 (Full control, approximately 2-3 days for MVP): Mercury self-build on a self-hosted server. Provides deterministic, logged, KB-integrated reviews with Tree-sitter AST context awareness. Phase 1 experience informs the optimal prompting strategy before committing infrastructure investment.

### 7.2 Mercury Self-Build MVP Data Flow

Data flow summary:
- GitHub PR Event (opened/synchronize/reopened) triggers HTTP POST with X-Hub-Signature-256 header
- Cloudflare Tunnel forwards to the server's webhook port
- FastAPI Webhook Receiver verifies HMAC-SHA256 signature using timingSafeEqual
- Review Pipeline fetches PR files, performs Tree-sitter AST parse, builds 2-hop import graph, loads CLAUDE.md rules, and assembles context capped at 90K tokens
- Claude API (claude-sonnet-4-6) produces Pydantic ReviewOutput with reasoning (chain-of-thought), verdict, confidence, summary, and line_comments
- Confidence threshold routing: >=0.85 yields APPROVE, >=0.70 plus blocking issues yields REQUEST_CHANGES, <0.70 yields COMMENT
- GitHub REST API POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews with event, body, and comments array
- Optional Mercury KB logging to 04-dev/PR-REVIEWS/

### 7.3 Key Components Summary

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Webhook receiver | FastAPI plus uvicorn | Receive and verify GitHub webhook events |
| AST parser | Tree-sitter Python bindings | Extract function signatures and import graph |
| LLM client | Anthropic Python SDK | Call claude-sonnet-4-6 API |
| Structured output | Pydantic ReviewOutput model | Enforce verdict plus line comments schema |
| GitHub client | httpx with GitHub REST API | Fetch PR files and post review |
| Network tunnel | Cloudflare cloudflared | Stable public webhook URL at no cost |
| Cache | SQLite file in mounted Docker volume | AST parse cache and review history log |

### 7.4 Why Not pr-agent or kodus-ai

pr-agent: Adds AGPL-3.0 dependency requiring source disclosure if modified and served over a network. Requires running a complete separate service that duplicates Mercury existing agent dispatch capability. No Mercury KB integration without significant custom development. Appropriate only if zero custom development time is acceptable.

kodus-ai: Enterprise EE feature paywall creates uncertainty about which production capabilities require a paid license. Large TypeScript monorepo adds significant operational overhead on self-hosted server. Mercury already implements equivalent agent orchestration via role-loader, task-manager, and orchestrator with full Claude API integration. Adding kodus-ai creates redundant parallel systems with no integration benefit.

---

## Sources

All technical claims verified against official vendor documentation.

1. PR-Agent changing a model: https://qodo-merge-docs.qodo.ai/usage-guide/changing_a_model/
2. PR-Agent GitHub installation: https://qodo-merge-docs.qodo.ai/installation/github/
3. Kodus AI official site: https://kodus.io/en/
4. Kodus AI GitHub repository: https://github.com/kodustech/kodus-ai
5. Kodus Enterprise license: https://github.com/kodustech/kodus-ai/blob/main/license_ee.md
6. Claude Code GitHub Actions official docs: https://code.claude.com/docs/en/github-actions
7. anthropics/claude-code-action repository: https://github.com/anthropics/claude-code-action
8. GitHub Webhook events and payloads: https://docs.github.com/en/webhooks/webhook-events-and-payloads
9. GitHub Validating webhook deliveries: https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
10. GitHub REST API endpoints for pull request reviews: https://docs.github.com/en/rest/pulls/reviews
11. GitHub REST API rate limits: https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
12. cAST AST-based code RAG chunking paper EMNLP 2025: https://arxiv.org/abs/2506.15655
13. ASTChunk open-source toolkit: https://github.com/yilinjz/astchunk
14. Pydantic AI output documentation: https://ai.pydantic.dev/output/
15. Pydantic LLM introduction: https://pydantic.dev/articles/llm-intro
16. (removed: vendor-specific host documentation)
17. (removed: vendor-specific host documentation)
18. FastAPI Docker deployment official docs: https://fastapi.tiangolo.com/deployment/docker/
19. Cloudflare Tunnel official documentation: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/
20. Cloudflare Tunnel Docker Compose community example: https://community.cloudflare.com/t/can-i-use-cloudflared-in-a-docker-compose-yml/407168
21. ngrok versus Cloudflare Tunnel comparison: https://dev.to/aryan_shourie/secure-tunneling-explained-ngrok-vs-cloudflared-mcl
