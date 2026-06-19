export const meta = {
  name: 'mercury-codebase-audit',
  description: 'Fan-out audit of a codebase across dimensions, with adversarial verification of each finding before it is reported',
  whenToUse: 'A repo-wide sweep that one conversation cannot hold: security/correctness/perf audit across many files. Each finding is adversarially verified so plausible-but-wrong findings are filtered out.',
  phases: [
    { title: 'Discover', detail: 'enumerate the audit work-list (capped)' },
    { title: 'Audit', detail: 'one agent per dimension — reads files itself' },
    { title: 'Verify', detail: 'adversarial skeptics refute each finding' },
    { title: 'Report', detail: 'dedup survivors + synthesize' },
  ],
}

// ── Mercury guardrails (see .mercury/docs/research/context-strategy-2026-05.md, #385) ──
// 1. LEAN DISPATCH: agents are given PATHS + a task, never the full file contents.
//    The agent reads what it needs itself — no bulk pre-injection into the prompt.
// 2. FAN-OUT CAP: the audit work-list is bounded; anything dropped is log()'d, never
//    silently truncated (a silent cap reads as "covered everything" when it didn't).
// 3. If a stage is ever routed to a haiku-tier model, keep its injected slice <= 50K
//    tokens (Haiku 4.5 is a 200K-ctx hard cliff). These templates inherit the session
//    model and pass only paths, so they stay well under that.

const target = (args && args.target) || '.'
const DIMENSIONS = (args && args.dimensions) || [
  { key: 'security', prompt: 'injection, authz/authn gaps, unsafe deserialization, secrets in code, SSRF, path traversal' },
  { key: 'correctness', prompt: 'logic defects, off-by-one, null/undefined handling, race conditions, error-swallowing' },
  { key: 'resource', prompt: 'unbounded loops/allocations, leaked handles, N+1 queries, missing timeouts/back-pressure' },
]
// Cap how many modules Discover may feed downstream, and how many findings per
// dimension flow into the (more expensive) verify stage. Both are enforced in code
// below (not just requested of the agent) and dropped overflow is log()'d.
const MODULE_CAP = (args && args.moduleCap) || 60
const MAX_FINDINGS = (args && args.maxFindings) || 40

const FINDINGS_SCHEMA = {
  type: 'object',
  required: ['findings'],
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['title', 'file', 'severity', 'evidence'],
        properties: {
          title: { type: 'string' },
          file: { type: 'string', description: 'path:line' },
          severity: { type: 'string', enum: ['high', 'medium', 'low'] },
          evidence: { type: 'string', description: 'concrete code evidence, not speculation' },
        },
      },
    },
  },
}

const VERDICT_SCHEMA = {
  type: 'object',
  required: ['isReal', 'reason'],
  properties: {
    isReal: { type: 'boolean' },
    reason: { type: 'string' },
  },
}

phase('Discover')
const inventory = await agent(
  `Enumerate the audit work-list for target \`${target}\`. Use Glob/Grep to list the source modules/directories worth auditing. ` +
  `Do NOT read file bodies in full — return a structured list of paths + a one-line role for each. Cap the list at the ${MODULE_CAP} highest-signal entries; ` +
  `if you had to drop entries, say how many.`,
  { phase: 'Discover', schema: {
    type: 'object',
    required: ['modules'],
    properties: {
      modules: { type: 'array', items: { type: 'object', required: ['path', 'role'], properties: { path: { type: 'string' }, role: { type: 'string' } } } },
      droppedCount: { type: 'number' },
    },
  } }
)
const rawModules = (inventory && inventory.modules) || []
// Enforce MODULE_CAP in code (don't trust the agent to honor the instruction), and
// surface every dropped module so partial coverage never reads as full coverage.
const modules = rawModules.slice(0, MODULE_CAP)
const overflow = rawModules.length - modules.length + ((inventory && inventory.droppedCount) || 0)
if (overflow > 0) log(`Discover capped at ${MODULE_CAP} modules — ${overflow} lower-signal modules dropped (fan-out cap)`)
log(`Auditing ${modules.length} modules across ${DIMENSIONS.length} dimensions`)
const moduleList = modules.map(m => `- ${m.path} (${m.role})`).join('\n')

// Pipeline: each dimension audits as soon as its findings are in, then each finding is
// adversarially verified — dimension B keeps auditing while dimension A's findings verify.
const verified = await pipeline(
  DIMENSIONS,
  d => agent(
    `Audit target \`${target}\` for the **${d.key}** dimension: ${d.prompt}. ` +
    `These are the modules in scope (read the ones relevant to this dimension yourself; do not assume their contents):\n${moduleList}\n` +
    `Return only findings backed by concrete code evidence with a path:line citation.`,
    { label: `audit:${d.key}`, phase: 'Audit', schema: FINDINGS_SCHEMA }
  ).then(r => {
    const all = (r && r.findings) || []
    const kept = all.slice(0, MAX_FINDINGS)
    if (all.length > kept.length) log(`${d.key}: ${all.length - kept.length} findings dropped at MAX_FINDINGS=${MAX_FINDINGS} (rerun narrower to verify them)`)
    return { dimension: d.key, findings: kept }
  }),
  res => parallel((res.findings || []).map(f => () =>
    agent(
      `Adversarially verify this ${res.dimension} finding. Read the cited code and try to REFUTE it. ` +
      `Default to isReal=false if the evidence does not clearly hold.\n` +
      `Finding: ${f.title}\nFile: ${f.file}\nClaimed evidence: ${f.evidence}`,
      { label: `verify:${f.file}`, phase: 'Verify', schema: VERDICT_SCHEMA }
    ).then(v => ({ ...f, dimension: res.dimension, verdict: v }))
  ))
)

phase('Report')
const confirmed = verified.flat().filter(Boolean).filter(f => f.verdict && f.verdict.isReal)
const order = { high: 0, medium: 1, low: 2 }
confirmed.sort((a, b) => (order[a.severity] ?? 3) - (order[b.severity] ?? 3))
log(`${confirmed.length} confirmed findings after adversarial verification`)

return {
  target,
  dimensions: DIMENSIONS.map(d => d.key),
  modulesAudited: modules.length,
  confirmedCount: confirmed.length,
  findings: confirmed,
}
