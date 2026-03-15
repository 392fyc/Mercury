# Mercury — Multi-Agent GUI Orchestrator

> One human + one Main Agent + N Sub Agents = automated multi-agent collaboration

## What is Mercury?

Mercury is a desktop GUI application that enables a human operator to manage multiple AI coding agents (Claude Code, Codex CLI, opencode, Gemini CLI, etc.) through a unified interface. The Main Agent can directly open, drive, and monitor Sub Agent sessions — eliminating manual copy-paste relay that plagues current multi-agent workflows.

## Status

🔬 **Research Phase** — Gathering requirements, evaluating tech stacks, building PoC

## Project Structure

```
D:\Mercury\
├── README.md                          # This file
├── docs/
│   ├── design/
│   │   ├── existing-workflow-analysis.md  # SoT multi-agent pain points
│   │   ├── sot-management-patterns.md     # Reusable patterns from SoT
│   │   └── mercury-bootstrap-prompt.md    # Project bootstrap + agent prompt
│   └── research/
│       └── agent-sdk-landscape.md         # SDK/API interface survey
└── (src/ — to be created after tech stack decision)
```

## Key Documents

| Document | Purpose |
|----------|---------|
| [Existing Workflow Analysis](docs/design/existing-workflow-analysis.md) | What works, what doesn't, in our current multi-agent setup |
| [SoT Management Patterns](docs/design/sot-management-patterns.md) | Reusable patterns: Task Bundle, Registry, orchestration modes |
| [Bootstrap Prompt](docs/design/mercury-bootstrap-prompt.md) | Full project spec + agent research tasks |
| [Agent SDK Landscape](docs/research/agent-sdk-landscape.md) | SDK/API interfaces for Claude Code, Codex, opencode, Gemini |

## Origin

Born from the Ship of Theseus game development project, where a multi-agent workflow (Claude Code as Main Agent + Codex CLI / opencode / AntiGravity as Sub Agents) proved effective but suffered from **36+ manual relay operations** per task cycle. Mercury aims to automate this relay entirely.
