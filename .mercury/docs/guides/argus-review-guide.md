# Argus — Automated PR Review Agent

Self-hosted PR review agent for Mercury, replacing CodeRabbit SaaS.

## Identity

- **GitHub App**: [Argus-review](https://github.com/apps/argus-review)
- **Bot user**: `argus-review[bot]`
- **Backend**: PR-Agent 0.34 + GPT-5.3-Codex
- **Deployed on**: QNAP NAS via Docker + Cloudflare Tunnel

## Commands

Post these as PR comments to trigger Argus:

| Command | Action |
|---------|--------|
| `/review` | Full review: summary (GitHub Review object) + inline threads on code lines |
| `/improve` | Code suggestions as inline threads with committable blocks |
| `/describe` | Auto-generate PR description |
| `/ask <question>` | Ask a question about the PR |

## Auto Triggers

On PR open: `/describe` + `/review` + `/improve`
On new commit push: `/describe` + `/review`

## Review Output Format

`/review` posts a **single GitHub Review** containing:
1. **Summary body**: PR Reviewer Guide (effort, security, ticket compliance)
2. **Inline threads**: Key issues on specific code lines, each with:
   - Severity badge (Critical/Major/Medium/Minor)
   - Problem description
   - `🤖 Prompt for AI Agents` (collapsible, English)

`/improve` posts **inline code threads** with:
- Severity + label
- `📝 Committable suggestion` (one-click apply)
- `🤖 Prompt for AI Agents`

## Configuration

Repo-level overrides: `.pr_agent.toml` (in Mercury repo root)
Server-level config: `configuration.toml` (on NAS, managed via [Argus repo](https://github.com/392fyc/Argus))

## User Whitelist

Only users listed in `ARGUS_ALLOWED_USERS` env var on the NAS can trigger reviews. Bot users (`[bot]`) are auto-allowed.

## Infrastructure

| Component | Location |
|-----------|----------|
| Repo | https://github.com/392fyc/Argus |
| Webhook URL | `https://argus.fyc-space.uk/api/v1/github_webhooks` |
| NAS config | `/share/homes/392fyc/argus/` |
| CI/CD | Push to Argus master → auto-deploy to NAS via GitHub Actions + cloudflared SSH |
| Domain | `fyc-space.uk` (Cloudflare Tunnel) |

## Differences from CodeRabbit

| Aspect | CodeRabbit | Argus |
|--------|-----------|-------|
| Trigger | `@coderabbitai review` | `/review` |
| CI check | Creates CI check | No CI check (uses GitHub Review objects) |
| Approval | Can submit APPROVED | Cannot self-approve owner PRs |
| Hosting | SaaS | Self-hosted on NAS |
| Cost | $24/mo/dev | ~$0.10/review (API tokens) |
