DISAGREE — frontend test infrastructure is out of scope for this Issue.

The mercury-gui package has zero frontend test infrastructure at HEAD:

```
$ ls mercury-gui/src/__tests__/ mercury-gui/src/hooks/__tests__/
ls: cannot access 'mercury-gui/src/__tests__/': No such file or directory
ls: cannot access 'mercury-gui/src/hooks/__tests__/': No such file or directory
$ grep -E '"vitest"|"@testing-library"' mercury-gui/package.json
(no matches)
```

There is no Vitest, no jsdom, no `@testing-library/react`, no test runner script, and no historical frontend test files in the package. Adding test coverage for the auto-refresh timer + visibility events + cleanup behavior would require **first** standing up that infra (devDependencies, `vitest` config, jsdom environment, type definitions, build-script entry, CI wiring), which is a much larger scope than this PR.

The Issue #436 Definition of Done explicitly scoped automated verification to **(a)** `pnpm build` cleanliness for TypeScript and **(b)** preserving the Rust-side `cargo test --lib 55/55` — both verified green on the current branch. The DoD did not require new frontend test coverage, and the rest of the dashboard slice (#416 / #425) shipped under the same convention.

Spinning up Vitest + jsdom on Tauri's webview lifecycle (`document.visibilityState`, `setInterval` fake timers, React 19 `useEffect` cleanup semantics under StrictMode) is a meaningful Issue in its own right — one that would affect the entire `mercury-gui/` package, not just this PR. That work belongs in a separate Issue scoped to:

- frontend test runner adoption decision (Vitest vs Jest)
- jsdom vs happy-dom for Tauri-emulating environment
- `@testing-library/react` + `@testing-library/user-event` adoption
- CI test-job wiring + coverage policy

Filed as follow-up Issue **#438** against the #427 v2 backlog parent so the scope is captured. This PR's auto-refresh logic is verified behaviorally per the DoD checklist: pnpm build clean (294.31 kB bundle), Rust cargo test 55/55, manual code-review (dual-verify PASS pre-commit + 4 Argus iters of progressive refinement). Per CLAUDE.md: "Don't add features beyond what the task requires."
