---
name: web-research
description: |
  Use this skill before writing code that depends on an external SDK, API, package, CLI, config key, environment variable, or version claim. Trigger proactively on English and Chinese requests such as "research", "verify", "validate", "check docs", "SDK", "API", "CLI", "npm version", "PyPI", "研究", "验证", "查文档", "查官方文档", "核对版本", "审查集成". This skill enforces Mercury's rule to check official vendor docs first, cross-check npm or PyPI for publish status, include source URLs, and mark unresolved claims as `UNVERIFIED`.
---

# Web Research

## When

- Use before coding against any third-party SDK, API, CLI, package, or hosted service.
- Use when you need a current method signature, published package name, install command, version, flag, or config key.
- Use even if the user did not explicitly ask for research; Mercury treats external API guesses as unsafe.
- Do not use this skill for purely internal project APIs unless external behavior is involved.

## Pipeline

1. List every external claim you are about to rely on:
   - package name
   - import path
   - version
   - method or constructor signature
   - CLI flags
   - config keys or environment variables
2. Verify each claim in this order:
   - official docs site
   - npm or PyPI package page
   - official changelog or release notes
   - official README only as a supplement
3. Treat these as insufficient on their own:
   - repo source code without docs
   - third-party blogs
   - Stack Overflow
   - stale snippets from memory
4. Record the authoritative details you confirmed:
   - exact URL
   - page date or version when available
   - exact behavior you are relying on
5. If a claim is not documented or browsing is unavailable:
   - mark it as `UNVERIFIED`
   - say what you searched for
   - stop and escalate if the missing fact is critical
6. Only implement code that relies on verified claims.

## Output

Use a compact research note before or alongside implementation:

```text
Claim:
Source:
Verified value:
Status: VERIFIED | UNVERIFIED
```

- Include source URLs directly in the response or handoff.
- If multiple claims were checked, group them by dependency.
- If the result is ambiguous, say so instead of collapsing competing interpretations.

## Evidence

- Preserve the official doc URL and the npm or PyPI URL for each external dependency.
- Keep the verified package name, version, import path, and signature you actually used.
- If anything stayed `UNVERIFIED`, state the gap explicitly in the final answer or code comment.
