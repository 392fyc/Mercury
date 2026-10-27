# Design philosophy

The portable layer defines collaboration contracts, not project policy.

1. **Small ownership surface.** Only file paths and the schema-fixed
   `mercury-template.lock` basename declared in `manifest.json` are generated,
   and every generated basename begins with `mercury-`.
2. **Downstream overlays remain authoritative.** Project-specific agents,
   rules, skills, configuration, and domain knowledge are never generated.
3. **Evidence precedes claims.** Tasks declare acceptance criteria; receipts
   cite fresh verification evidence; review and acceptance remain separate.
4. **Repositories choose their mechanics.** The template describes safe Git
   outcomes without depending on a branch name convention, wrapper script, CI
   provider, local filesystem layout, or orchestration implementation. Listed
   direct Git prefix rules are incomplete defense in depth rather than an
   arbitrary shell-command inspector: shell trampolines and unlisted token
   forms must obey project instructions and the controlled entrypoint, while
   remote branch protection is the final publication boundary.
5. **Deterministic provenance.** The manifest and template blobs come from the
   recorded Git commit. The generated `.codex/mercury-template.lock` makes the
   exact source revision, ownership set, and content hashes inspectable without
   storing local state. An existing lock is trusted only when Mercury can
   reconstruct its canonical bytes from the recorded commit.
6. **Detectable partial updates.** Generated files are replaced atomically one
   at a time and the lock is written last. This is intentionally retry-safe,
   not a whole-tree transaction; `check` exposes an interrupted update.
7. **Exclusive caller ownership.** Path checks cover ordinary symlinks,
   junctions, reparse points, and hard links. The caller must prevent
   adversarial concurrent mutation while synchronization is running.

The result is deliberately detachable: deleting the generated `mercury-*`
files removes the generic layer without disturbing the downstream project.
