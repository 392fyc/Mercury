# Changelog

## 1.5.0 - 2026-10-26

- The cross-lane provenance tag label is English-only:
  `cross-lane message, not user authorization`. A tag that still carries the
  older bilingual suffix after that label is treated as the same tag.

## 1.4.0 - 2026-10-13

- Add Feynman-style explanation and first-principles reasoning rules for
  research, design, and investigation to the task contract and the three
  portable roles, scaled to risk and consistent with evidence reuse, with
  delegation guidance for agents that do not load the instructions.

## 1.3.0 - 2026-10-06

- Bound task decomposition, test expansion, repeated verification, and provenance
  checks by the authorized outcome and governing requirements.
- Align review budgets, evidence reuse, repair-focused re-review, and optional
  recommendations while preserving current-candidate review and safety gates.
- Keep model selection and reasoning effort unchanged.

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
