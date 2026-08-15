---
name: research-web-verify
description: Always verify external SDK/API/CLI claims via WebSearch before writing code or documentation.
category: TOOL_GUIDE
roles:
  - research
  - dev
  - main
origin: IMPORTED
tags:
  - web-search
  - verification
  - api
  - sdk
generation: 0
parent_skill_ids: []
total_selections: 0
total_applied: 0
total_completions: 0
total_fallbacks: 0
last_validated_at: 2026-04-04T00:00:00.000Z
---

# Web Verification Protocol for External References

## Mandatory Rule

Before writing ANY code or documentation that:
- Imports an external package
- References an API method signature
- Claims a package version number
- References a CLI flag or command

You MUST run WebSearch or WebFetch against the vendor's official documentation first.

## Protocol

1. **Search**: `WebSearch` with query: `<package-name> npm API <method> <year>`
2. **Fetch**: `WebFetch` on the official docs URL if available
3. **Cross-check**: Verify against npm registry page for versions
4. **Record**: Include source URLs when writing findings to KB

## When You Cannot Verify

If web verification fails (blocked, rate-limited, no results):
- Mark the claim as `UNVERIFIED` in the KB report
- Do not write code that depends on the unverified claim
- Escalate to Main Agent with the uncertainty noted

## Why This Matters

Training data is frozen at a cutoff date. Package APIs change, versions are deprecated, and signatures shift. A single unverified assumption can cascade into hours of debugging for the dev agent.
