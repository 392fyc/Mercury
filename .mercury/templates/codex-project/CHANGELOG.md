# Changelog

## 1.2.0 - 2026-10-05

- Add a portable lane contract for repositories that run several lanes and
  agents in parallel: user-declared lanes, per-agent handoffs, version
  preconditions for shared files, cross-lane provenance, controlled entries,
  and enforced write guards for read-only roles.

## 1.1.0 - 2026-10-03

- Add an explicit Mercury ownership contract and pin portable worker role
  configuration to GPT-6 Luna with maximum reasoning effort.
- Reject modified managed files before applying updates while allowing an
  interrupted update to resume when a file already contains its new bytes.

## 1.0.0 - 2026-08-15

- Publish the initial portable task, evidence, receipt, review, acceptance, and
  protected-branch safety contracts.
- Establish manifest-scoped ownership for generated `mercury-*` files.
