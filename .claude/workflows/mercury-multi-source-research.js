export const meta = {
  name: 'mercury-multi-source-research',
  description: 'Fan out web research across independent angles, cross-check each claim against multiple sources, and synthesize a cited report with unverified claims flagged',
  whenToUse: 'A research question that needs >=3 sources cross-checked against each other (SDK/API behavior, vendor pricing, version facts). The parallel cross-check replaces autoresearch\'s serial single-context loop. For a 1-2 source quick lookup, use the web-research skill instead.',
  phases: [
    { title: 'Sweep', detail: 'parallel agents, each a different research angle' },
    { title: 'CrossCheck', detail: 'vote each claim against independent sources' },
    { title: 'Synthesize', detail: 'cited report, UNVERIFIED claims flagged' },
  ],
}

// ── Mercury guardrails (#385 + CLAUDE.md "Web search before SDK/API code") ──
// 1. Each angle agent does its OWN WebSearch/WebFetch — the question is passed lean,
//    no pre-fetched page dumps are injected into sibling agents.
// 2. ANGLE_CAP bounds the sweep. Vendor/official sources are preferred; a claim that
//    cannot be web-verified is reported as UNVERIFIED rather than dropped or asserted.
// 3. Mirrors the bundled /deep-research but Mercury-flavored: official-docs-first,
//    explicit UNVERIFIED tagging per Mercury's mandatory research protocol.

const question = (args && (args.question || args.q)) || (typeof args === 'string' ? args : null)
if (!question) {
  log('No research question provided. Pass one via args, e.g. /mercury-multi-source-research "does X SDK support Y?"')
  return { error: 'missing question', hint: 'pass args as a string or { question }' }
}

// Caps are operator-overridable but clamped to a hard ceiling, so the #385 fan-out bound
// holds even under an oversized override (ANGLE_CAP is also bounded by the ANGLES array).
const ANGLE_CAP = Math.min((args && args.maxAngles) || 5, 8)
// Bound the SECOND fan-out too: a noisy question can surface dozens of claims, and
// cross-checking each spawns an agent. Cap + log dropped (#385: every fan-out is bounded).
const CLAIM_CAP = Math.min((args && args.maxClaims) || 24, 100)
const ANGLES = [
  'official vendor documentation and API reference',
  'package registry (npm/PyPI) version + publish status + changelog',
  'release notes / changelogs / migration guides',
  'authoritative third-party write-ups that corroborate or contradict the official line',
  'known issues, breaking changes, and community-reported gotchas',
].slice(0, ANGLE_CAP)

const CLAIMS_SCHEMA = {
  type: 'object',
  required: ['claims'],
  properties: {
    claims: {
      type: 'array',
      items: {
        type: 'object',
        required: ['claim', 'sources'],
        properties: {
          claim: { type: 'string' },
          sources: { type: 'array', items: { type: 'string', description: 'source URL' } },
          confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
        },
      },
    },
  },
}

const VOTE_SCHEMA = {
  type: 'object',
  required: ['status', 'reason'],
  properties: {
    status: { type: 'string', enum: ['verified', 'contradicted', 'unverified'] },
    reason: { type: 'string' },
    bestSource: { type: 'string' },
  },
}

phase('Sweep')
const swept = await parallel(ANGLES.map(angle => () =>
  agent(
    `Research this question from the angle of **${angle}**:\n"${question}"\n` +
    `Use WebSearch + WebFetch yourself. Prefer official/primary sources. Return each finding as a discrete claim with its source URL(s). ` +
    `Do not speculate beyond what a source supports.`,
    { label: `sweep:${angle.slice(0, 24)}`, phase: 'Sweep', schema: CLAIMS_SCHEMA, agentType: 'research' }
  )
))
const allClaims = swept.filter(Boolean).flatMap(r => (r && r.claims) || [])
// Prefer higher-confidence claims when the cap bites, so the cross-check budget is
// spent on the most load-bearing claims rather than an arbitrary prefix.
const rank = { high: 0, medium: 1, low: 2 }
allClaims.sort((a, b) => (rank[a.confidence] ?? 1) - (rank[b.confidence] ?? 1))
const claims = allClaims.slice(0, CLAIM_CAP)
if (allClaims.length > claims.length) log(`${allClaims.length - claims.length} lower-confidence claims dropped at CLAIM_CAP=${CLAIM_CAP} (rerun narrower to cross-check them)`)
log(`Cross-checking ${claims.length} of ${allClaims.length} candidate claims across ${ANGLES.length} angles`)

phase('CrossCheck')
// Each claim is independently re-verified against fresh sources (not the ones that
// produced it) so a single angle cannot self-confirm.
const voted = await parallel(claims.map(c => () =>
  agent(
    `Independently cross-check this claim against fresh authoritative sources (do NOT just trust the cited ones):\n` +
    `Claim: "${c.claim}"\nOriginally cited: ${(c.sources || []).join(', ') || '(none)'}\n` +
    `Use WebSearch/WebFetch. Return verified only if an official/primary source confirms it; contradicted if a source refutes it; ` +
    `unverified if you cannot confirm against a primary source.`,
    { label: 'crosscheck', phase: 'CrossCheck', schema: VOTE_SCHEMA, agentType: 'research' }
  ).then(v => ({ ...c, vote: v }))
))

phase('Synthesize')
const results = voted.filter(Boolean)
const verifiedClaims = results.filter(c => c.vote && c.vote.status === 'verified')
const contradicted = results.filter(c => c.vote && c.vote.status === 'contradicted')
const unverified = results.filter(c => c.vote && c.vote.status === 'unverified')
log(`${verifiedClaims.length} verified · ${contradicted.length} contradicted · ${unverified.length} UNVERIFIED`)

return {
  question,
  verified: verifiedClaims.map(c => ({ claim: c.claim, source: c.vote.bestSource || (c.sources || [])[0], reason: c.vote.reason })),
  contradicted: contradicted.map(c => ({ claim: c.claim, reason: c.vote.reason })),
  unverified: unverified.map(c => ({ claim: c.claim, reason: c.vote.reason })),
  note: 'Claims under `unverified` MUST be marked [UNVERIFIED] if used in code/docs (Mercury research protocol).',
}
