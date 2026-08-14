# Mercury Issue #439 — CSP Tier-2 Hardening Pre-Commit Audit

## Repo

D:/Mercury/Mercury — Mercury Phase 6 Tauri 2 desktop shell (mercury-gui).

## Branch

`feat/439-csp-tier2` (off `develop @ 6c57782`)

## Changed file

Single file: `mercury-gui/src-tauri/tauri.conf.json` (+2 / -1).

## Diff

```
@@ -19,7 +19,8 @@
       }
     ],
     "security": {
-      "csp": "default-src 'self'; img-src 'self' asset: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self' http://localhost:1420 ws://localhost:1421"
+      "csp": "default-src 'self'; img-src 'self' asset: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'none'",
+      "devCsp": "default-src 'self'; img-src 'self' asset: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self' http://localhost:1420 ws://localhost:1421; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'none'"
     }
   },
```

## Goal

Pre-commit code-audit per Mercury dual-verify gate (CLAUDE.md). This change implements #427 v2 backlog sub-item 7:

1. Splits production `csp` (no dev URLs) from `devCsp` (dev URLs kept for Vite HMR)
2. Adds defense-in-depth directives to BOTH: `object-src 'none'`, `frame-ancestors 'none'`, `base-uri 'self'`, `form-action 'none'`
3. KEEPS `style-src 'self' 'unsafe-inline'` in both — Radix UI Dialog uses `react-remove-scroll` which injects styles at runtime; removing `'unsafe-inline'` is blocked by Radix architecture

## Research already done (don't redo)

- Tauri 2 supports `devCsp` field separately from `csp` — verified via https://schema.tauri.app/config/2 and https://v2.tauri.app/security/csp/ (Tauri commit cf54dcf added devCsp)
- Tauri auto-injects nonces/hashes for bundled assets at compile time but cannot cover runtime DOM mutations by libraries like react-remove-scroll
- Vite HMR needs 'unsafe-inline' style-src in dev (vitejs/vite#11862)
- Radix Dialog runtime style injection cannot be removed without Base UI migration (Radix #2057, #3130 — out of scope)

## Verification already done

- JSON validity: python json.load OK, both csp + devCsp fields present
- cargo test --lib: 55/55 pass (Tauri build script accepts `devCsp` field)
- pnpm build: clean (294.31 kB JS unchanged, 31.66 kB CSS)
- No inline `style={}` or `style=""` in mercury-gui/src (grep verified) — Radix runtime is sole consumer of style-src 'unsafe-inline'

## Audit task

Return a structured verdict as:

```
=== VERDICT: PASS|FAIL ===
N Critical / N Major / N Minor / N Nit
```

followed by findings (file:line citations).

Audit focus:
- Tauri 2 CSP field name correctness (`devCsp` vs `dev_csp` etc.)
- CSP directive correctness — any malformed syntax?
- Defense-in-depth coverage — anything missing for a Tauri 2 SPA?
- Dev vs prod separation logic — does the split correctly preserve HMR while tightening prod?
- Any unintended widening (e.g., did we accidentally relax something)?

Carry-forward praise allowed. ≤500 words.
