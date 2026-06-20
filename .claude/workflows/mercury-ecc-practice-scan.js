export const meta = {
  name: 'mercury-ecc-practice-scan',
  description: 'Research everything-claude-code (ECC) recent practices, adversarially verify each against fresh sources, then map every non-contradicted practice (verified + unverified) to Mercury (already-covered / worth-absorbing / not-applicable). Each mapped item carries its verify status; unverified candidates are flagged (also listed in the unverified bucket), never silently promoted to adoption.',
  whenToUse: 'Periodic re-audit of the everything-claude-code community aggregator for newly-surfaced Claude Code orchestration practices worth selectively cherry-picking into Mercury. Aligns with #233 (ECC v2.0 audit) — does NOT file issues, only produces a verified + Mercury-mapped report.',
  phases: [
    { title: 'Recon', detail: 'locate ECC repo + sweep recent practices from repo + community write-ups' },
    { title: 'Verify', detail: 'independently cross-check each practice against fresh sources, UNVERIFIED-tag' },
    { title: 'MapToMercury', detail: 'classify each non-contradicted practice (verified + unverified, unverified flagged) vs the Mercury codebase' },
  ],
}

// ── Mercury #385 context-economics guardrails (this Workflow obeys them) ──
// 1. NO pre-injection of full docs. Map agents get Mercury repo PATHS + the task; they
//    Read/Grep themselves. Recon/verify agents get the question lean + do their own web fetch.
// 2. Every fan-out is capped (RECON angles / PRACTICE_CAP) and anything dropped is log()'d.
// 3. Mandatory research protocol: each practice carries a source URL; a practice that cannot
//    be web-verified is reported UNVERIFIED, never asserted or silently dropped.
// 4. ECC background: MIT, monolithic harness — Mercury DEFERs wholesale mount but allows
//    modular cherry-pick (see #233 + .mercury/docs/research/ecc-practice-scan-2026-06.md).
//    This scan finds cherry-pick candidates.

const focus = (args && (args.focus || args.q)) || (typeof args === 'string' ? args : null) ||
  'newly-added or recently-shared Claude Code orchestration practices, hooks, subagent/skill patterns, and workflow techniques in everything-claude-code over roughly the last 1-3 months'

// Mercury repo paths the MapToMercury stage consults (lean: paths, not contents).
const MERCURY_CTX_PATHS = (args && args.mercuryPaths) || [
  'CLAUDE.md',
  '.claude/workflows/README.md',
  '.claude/agents/main.md',
  '.claude/agents/ (dev/acceptance/critic/research/design + game-* roles)',
  '.claude/settings.json + adapters/mercury-test-gate/ (SubagentStop test gate)',
  '.mercury/docs/research/harness-modernization-survey-2026-06.md (2026 Q1-Q2 primitive absorption)',
  '.mercury/docs/research/context-strategy-2026-05.md (#385 context guardrails ADR)',
  '.mercury/docs/DIRECTION.md (supreme reference)',
]

// Caps (operator-overridable, clamped). #385: bound every fan-out, log the remainder.
const RECON_CAP = Math.max(1, Math.min((args && args.maxAngles) || 5, 8))
const PRACTICE_CAP = Math.max(1, Math.min((args && args.maxPractices) || 24, 60))

const RECON_ANGLES = [
  'the everything-claude-code GitHub repository itself — locate the canonical repo (owner/name + URL), then read its README, directory/section structure, and any CHANGELOG/releases to catalog what practices it currently ships',
  'recent commits, merged PRs, and new sections/files added to the everything-claude-code repo in roughly the last 1-3 months (what is NEW vs the older baseline)',
  'community write-ups, blog posts, X/Reddit/HN threads, and newsletters that discuss everything-claude-code practices or that ECC aggregates from',
  'Claude Code orchestration techniques (hooks, subagents, skills, workflows, MCP, output styles, statusline, permissions) that ECC newly demonstrates and that map to a concrete, copyable artifact',
  'known caveats, deprecations, or community-reported problems with the ECC practices (so we do not cherry-pick a broken/abandoned pattern)',
].slice(0, RECON_CAP)

const PRACTICES_SCHEMA = {
  type: 'object',
  required: ['practices'],
  properties: {
    practices: {
      type: 'array',
      items: {
        type: 'object',
        required: ['name', 'summary', 'sources'],
        properties: {
          name: { type: 'string', description: 'short practice name' },
          summary: { type: 'string', description: 'what the practice is + the concrete artifact (file/hook/skill/command) it maps to' },
          sources: { type: 'array', minItems: 1, items: { type: 'string', description: 'source URL' }, description: 'at least one source URL (guardrail §3: every practice carries a source; unverifiable ones are caught at the Verify stage)' },
          recency: { type: 'string', enum: ['new', 'updated', 'established', 'unknown'], description: 'is this newly added/updated in ECC or a long-standing pattern' },
        },
      },
    },
  },
}

const VERIFY_SCHEMA = {
  type: 'object',
  required: ['status', 'reason', 'bestSource'],
  properties: {
    status: { type: 'string', enum: ['verified', 'contradicted', 'unverified'] },
    reason: { type: 'string' },
    bestSource: { type: 'string', description: 'authoritative URL the verdict rests on, or "none"' },
  },
}

const MAP_SCHEMA = {
  type: 'object',
  required: ['verdict', 'rationale'],
  properties: {
    verdict: { type: 'string', enum: ['already-covered', 'worth-absorbing', 'not-applicable'] },
    rationale: { type: 'string', description: 'why, citing the Mercury file(s) that already cover it OR the gap it fills' },
    mercuryEvidence: { type: 'array', items: { type: 'string', description: 'Mercury file:path (or section) supporting the verdict' } },
    absorbCost: { type: 'string', enum: ['trivial', 'small', 'medium', 'large', 'n/a'], description: 'rough effort to cherry-pick if worth-absorbing, else n/a' },
  },
}

phase('Recon')
const swept = await parallel(RECON_ANGLES.map(angle => () =>
  agent(
    `Research everything-claude-code (ECC) from this angle:\n**${angle}**\n\n` +
    `Overall focus: ${focus}\n\n` +
    `Use WebSearch + WebFetch yourself. First confirm the canonical ECC repo identity (owner/name + URL) from a primary source. ` +
    `Return each distinct practice as a discrete item with: a short name, a summary that names the CONCRETE artifact it maps to ` +
    `(a hook script, a subagent definition, a skill, a slash command, a settings pattern, etc.), source URL(s), and whether it is new/updated/established. ` +
    `Do not invent practices — only report what a source actually shows.`,
    { label: `recon:${angle.slice(0, 22)}`, phase: 'Recon', schema: PRACTICES_SCHEMA, agentType: 'research' }
  )
))

// Dedup by practice name before the cap so duplicates don't eat PRACTICE_CAP budget.
const sweptPractices = swept.filter(Boolean).flatMap(r => (r && r.practices) || [])
const seen = new Set()
const distinct = []
for (const p of sweptPractices) {
  const k = (p && p.name || '').trim().toLowerCase().replace(/\s+/g, ' ')
  if (!k || seen.has(k)) continue
  seen.add(k)
  distinct.push(p)
}
if (sweptPractices.length > distinct.length) log(`Deduped ${sweptPractices.length - distinct.length} duplicate practices across recon angles`)
// Prefer new/updated practices when the cap bites — that's the point of a re-audit.
const recRank = { new: 0, updated: 1, established: 2, unknown: 3 }
distinct.sort((a, b) => (recRank[a.recency] ?? 3) - (recRank[b.recency] ?? 3))
const practices = distinct.slice(0, PRACTICE_CAP)
if (distinct.length > practices.length) log(`${distinct.length - practices.length} practices dropped at PRACTICE_CAP=${PRACTICE_CAP} (rerun narrower to cover them)`)
log(`Recon surfaced ${distinct.length} distinct practices; verifying ${practices.length}`)

phase('Verify')
// Pipeline: each practice is verified, then (if not contradicted) mapped to Mercury — no barrier
// between the two stages, so a practice maps as soon as its verification lands.
const mapped = await pipeline(
  practices,
  (p, _orig, i) => agent(
    `Independently cross-check this everything-claude-code practice against FRESH authoritative sources (do not just trust the cited ones):\n` +
    `Practice: "${p.name}" — ${p.summary}\nOriginally cited: ${(p.sources || []).join(', ') || '(none)'}\n\n` +
    `Use WebSearch/WebFetch. verified = a primary/authoritative source confirms the practice exists in ECC as described; ` +
    `contradicted = a source refutes it; unverified = cannot confirm against a real source.`,
    { label: `verify:${i + 1}:${(p.name || '').slice(0, 18)}`, phase: 'Verify', schema: VERIFY_SCHEMA, agentType: 'research' }
  ).then(v => ({ ...p, verify: v })),
  (pv, _orig, i) => {
    // Skip mapping for contradicted practices — no point placing a refuted pattern.
    if (!pv || !pv.verify || pv.verify.status === 'contradicted') return pv
    return agent(
      `You are auditing whether an external practice is already covered by the Mercury harness.\n\n` +
      `Practice from everything-claude-code: "${pv.name}" — ${pv.summary}\n` +
      `Verification status: ${pv.verify.status} (${pv.verify.reason})\n\n` +
      `Consult these Mercury repo paths (Read/Grep them yourself — do NOT assume contents):\n` +
      MERCURY_CTX_PATHS.map(p => `  - ${p}`).join('\n') + '\n\n' +
      `Decide ONE verdict:\n` +
      `- already-covered: Mercury already implements an equivalent (cite the file/section).\n` +
      `- worth-absorbing: a real gap this would fill; estimate cherry-pick cost. Respect Mercury's adapters<=200LOC + modular-detachability rules and the ECC wholesale-mount DEFER (only modular cherry-pick is allowed).\n` +
      `- not-applicable: does not fit Mercury's architecture/constraints (say why).\n` +
      `Cite Mercury file paths as evidence. Do not recommend re-mounting ECC wholesale.`,
      { label: `map:${i + 1}:${(pv.name || '').slice(0, 18)}`, phase: 'MapToMercury', schema: MAP_SCHEMA, agentType: 'research' }
    ).then(m => ({ ...pv, map: m }))
  }
)

phase('MapToMercury')
const results = mapped.filter(Boolean)
const verified = results.filter(r => r.verify && r.verify.status === 'verified')
const contradicted = results.filter(r => r.verify && r.verify.status === 'contradicted')
const unverified = results.filter(r => r.verify && r.verify.status === 'unverified')
const absorb = results.filter(r => r.map && r.map.verdict === 'worth-absorbing')
const covered = results.filter(r => r.map && r.map.verdict === 'already-covered')
const na = results.filter(r => r.map && r.map.verdict === 'not-applicable')
log(`${verified.length} verified · ${contradicted.length} contradicted · ${unverified.length} UNVERIFIED`)
log(`Mercury map: ${absorb.length} worth-absorbing · ${covered.length} already-covered · ${na.length} not-applicable`)

const shape = r => ({
  name: r.name,
  summary: r.summary,
  recency: r.recency,
  status: r.verify && r.verify.status,
  bestSource: (r.verify && r.verify.bestSource) || (r.sources || [])[0] || 'none',
  verdict: r.map && r.map.verdict,
  absorbCost: r.map && r.map.absorbCost,
  rationale: r.map && r.map.rationale,
  mercuryEvidence: (r.map && r.map.mercuryEvidence) || [],
})

return {
  focus,
  worthAbsorbing: absorb.map(shape),
  alreadyCovered: covered.map(shape),
  notApplicable: na.map(shape),
  contradicted: contradicted.map(r => ({ name: r.name, reason: r.verify.reason })),
  unverified: unverified.map(r => ({ name: r.name, reason: r.verify.reason, summary: r.summary })),
  note: 'worthAbsorbing/alreadyCovered/notApplicable items carry a `status` field — any with status!=="verified" are candidates PENDING verification (the same item also appears in the `unverified` bucket) and MUST be tagged [UNVERIFIED] + re-verified before adoption (Mercury research protocol). Only contradicted practices are excluded from mapping. Align with #233; do NOT file duplicate issues.',
}
