export const meta = {
  name: 'mercury-staleness-audit',
  description: 'Tier-2 of Mercury\'s upstream-staleness mechanism: an LLM audit of Mercury for stale / expired / dead external dependencies still in use — cherry-picks that drifted upstream, pinned versions behind upstream, and disabled/retired components still referenced. Ground-truths each item, classifies (ACTIVE-RISK / ACTION-NEEDED / ACCEPTABLE-DRIFT / DORMANT-OK / NOT-STALE / UNVERIFIED), adversarially re-checks every classification, and recommends actions. Read-only — files no issues.',
  whenToUse: 'Periodic (quarterly, or after a Tier-1 upstream-drift.yml alarm) deep staleness review that the deterministic blob-drift checker (scripts/upstream-drift-check.sh) cannot do: judging HOW FAR behind a pin is, whether a component has gone dead/disabled, and whether an upstream went archived. Aligns with Issue #508. Does NOT file issues — produces a classified report that feeds the operator\'s triage + any follow-up Issue/PR.',
  phases: [
    { title: 'Discover', detail: 'find pinned / cherry-picked / plugin external deps that could go stale' },
    { title: 'Verify', detail: 'ground-truth current upstream state + classify each item' },
    { title: 'Adversarial', detail: 'skeptic re-checks each classification' },
    { title: 'Synthesize', detail: 'findings table + recommended actions' },
  ],
}

// ── Mercury #385 context-economics guardrails (this Workflow obeys them) ──
// 1. NO pre-injection of full docs. Items carry lean facts + repo PATHS; agents
//    Read/Grep the cited files and WebSearch/WebFetch the current upstream state
//    themselves. No file contents are bulk-injected into sibling agents.
// 2. Every fan-out is capped (ITEM_CAP) and anything dropped is log()'d.
// 3. Mandatory research protocol: a component whose upstream state cannot be
//    web-verified is classified UNVERIFIED, never silently dropped or asserted.
//
// The SEED below is a curated, EVERGREEN set of known dead-component / freeze
// risks that a pure manifest grep would miss (a disabled model, a frozen review
// base, a plugin version). It is phrased as check-DIRECTIVES, not point-in-time
// facts, so the committed template does not rot — the agent fills in current
// state at run time. The Discover agent finds everything else (manifest, adapter
// pins, MCP servers, plugins, lockfiles, model IDs). Operators may extend the
// seed via args.seedItems (array of { key, claim, refs }).

const SEED_ITEMS = [
  { key: 'disabled-model-routing', claim: 'CHECK whether any model Mercury references is currently disabled / suspended / retired by the vendor (e.g. an Anthropic model under an external directive), AND whether anything in Mercury actively ROUTES to it (agent frontmatter `model:`, workflow opts.model, scripts) vs merely carrying a defensive cost-tracker PRICING entry. A defensive price entry for a dead model is DORMANT-OK; an actual routing target that is dead is ACTIVE-RISK.', refs: '~/.claude/scripts/cost_tracker.py PRICING (user-scope); .claude/agents/*.md model frontmatter; .claude/workflows/*.js opts.model; memory reference_fable5_disabled_remind_on_reenable' },
  { key: 'omc-plugin-version', claim: 'CHECK how far the installed oh-my-claudecode plugin is behind its upstream marketplace/npm release, and whether Mercury depends on any OMC feature that changed/broke. OMC is a committed plugin dependency (enabledPlugins "oh-my-claudecode@omc": true), plugin-only (no submodule).', refs: '.claude/settings.json enabledPlugins; CLAUDE.md (OMC orchestration layer)' },
  { key: 'review-bot-base-freeze', claim: 'CHECK how far Mercury\'s Argus PR-review base image is behind canonical upstream pr-agent, and whether the freeze is causing active malfunction vs being a tracked deferred-maintenance item. Note the codiumai/ Docker namespace is frozen and new releases publish under a different namespace.', refs: 'Issue #498 (fork-rebase tracker); .pr_agent.toml; memory project_476_argus_fixes_implemented' },
  { key: 'cherry-pick-drift', claim: 'CHECK the cherry-picked skill/agent files in the manifest whose upstream blob changed since import (the CHANGED set is whatever scripts/upstream-drift-check.sh reports — Read the manifest entries and WebFetch each upstream file at import-SHA vs HEAD to confirm): do the upstream changes carry a material improvement/fix Mercury should pull, or are they cosmetic / superseded by Mercury-owned adaptations (no live-sync obligation under the cherry-pick protocol)?', refs: '.mercury/state/upstream-manifest.json; .claude/skills/ + .claude/agents/ cherry-picked entries; scripts/upstream-drift-check.sh' },
  { key: 'adapter-version-pins', claim: 'CHECK each adapters/*/ external runtime pin (npm-version-pin, uvx git+SHA, exact crate version) against current upstream latest: how far behind, any security advisory in the gap, any breaking change that Mercury\'s adapter actually reaches (vs blocked by an allowlist/wrapper). A CLEAN blob-drift only means the version-contract file is unchanged — it does NOT confirm the live endpoint/model is alive.', refs: 'adapters/*/UPSTREAM.md + launch wrappers + invoke.py; .mercury/state/upstream-manifest.json; pnpm-lock.yaml; *.Cargo.toml' },
]

const ITEM_CAP = Math.max(1, Math.min((args && args.maxItems) || 16, 32))
const _operatorSeed = (args && Array.isArray(args.seedItems)) ? args.seedItems.filter(s => s && s.key && s.claim) : []

const DISCOVER_SCHEMA = {
  type: 'object',
  required: ['items'],
  properties: {
    items: {
      type: 'array',
      items: {
        type: 'object',
        required: ['key', 'claim'],
        properties: {
          key: { type: 'string', description: 'short kebab-case id' },
          claim: { type: 'string', description: 'the pinned/external dep + what to CHECK for staleness' },
          refs: { type: 'string', description: 'Mercury file paths / issue refs evidencing it' },
        },
      },
    },
  },
}

const VERIFY_SCHEMA = {
  type: 'object',
  required: ['classification', 'verdict', 'impact'],
  properties: {
    classification: { type: 'string', enum: ['ACTIVE-RISK', 'ACTION-NEEDED', 'ACCEPTABLE-DRIFT', 'DORMANT-OK', 'NOT-STALE', 'UNVERIFIED'] },
    verdict: { type: 'string', description: 'one-line current-state finding' },
    currentUpstream: { type: 'string', description: 'current upstream version/state discovered (or "unknown")' },
    pinnedOrUsed: { type: 'string', description: 'what Mercury pins/uses' },
    impact: { type: 'string', description: 'concrete impact if stale — (i) local problem from outdated component, or (ii) wrong use of a dead/disabled component; "none" if benign' },
    recommendedAction: { type: 'string' },
    sources: { type: 'array', items: { type: 'string', description: 'source URL' } },
  },
}

const ADV_SCHEMA = {
  type: 'object',
  required: ['agree', 'revisedClassification', 'reason'],
  properties: {
    agree: { type: 'boolean', description: 'does the skeptic agree with the original classification' },
    revisedClassification: { type: 'string', enum: ['ACTIVE-RISK', 'ACTION-NEEDED', 'ACCEPTABLE-DRIFT', 'DORMANT-OK', 'NOT-STALE', 'UNVERIFIED', 'UNCHANGED'] },
    concerns: { type: 'array', items: { type: 'string' } },
    reason: { type: 'string' },
  },
}

phase('Discover')
const seedKeys = SEED_ITEMS.concat(_operatorSeed).map(s => s.key).join(', ')
const disc = await agent(
  `Discover Mercury external dependencies that could go stale/expired/dead and are NOT already in this seed list: [${seedKeys}].\n\n` +
  `Read/Grep the Mercury repo yourself. Look in: .mercury/state/upstream-manifest.json (cherry-pick ledger), adapters/*/ (package.json / pyproject pins / UPSTREAM.md / launch wrappers), .mcp.json (MCP server commands + versions), .claude/settings.json enabledPlugins, .gitmodules (git submodules if any), Cargo.toml / pnpm-lock.yaml pins, and any pinned model IDs / SHAs / npm / crate versions referenced in scripts or agent frontmatter. ` +
  `Return only NEW items (distinct from the seed keys), each with a short key, a claim stating the pinned thing + what to CHECK for staleness, and refs. If nothing new, return an empty items array.`,
  { label: 'discover-deps', phase: 'Discover', schema: DISCOVER_SCHEMA, agentType: 'research' }
)
const discovered = (disc && Array.isArray(disc.items)) ? disc.items : []
// Dedup by key, seeds (built-in + operator) first so the cap drops the discovered
// tail before any seed. Count seeded items POST-dedup (seededInItems) so the log
// stays correct even when an operator seed duplicates a built-in seed key.
const seenKeys = new Set()
const ITEMS = []
let seededInItems = 0
for (const it of SEED_ITEMS.concat(_operatorSeed)) {
  const k = (it && it.key || '').trim().toLowerCase()
  if (!k || seenKeys.has(k)) continue
  seenKeys.add(k); ITEMS.push(it); seededInItems++
}
for (const it of discovered) {
  const k = (it && it.key || '').trim().toLowerCase()
  if (!k || seenKeys.has(k)) continue
  seenKeys.add(k); ITEMS.push(it)
}
if (ITEMS.length > ITEM_CAP) { log(`${ITEMS.length - ITEM_CAP} items dropped at ITEM_CAP=${ITEM_CAP} (rerun to cover them)`); ITEMS.length = ITEM_CAP }
const seededShown = Math.min(seededInItems, ITEMS.length)
log(`Auditing ${ITEMS.length} stale-dependency items (${seededShown} seeded + ${ITEMS.length - seededShown} discovered)`)

phase('Verify')
// Pipeline: each item is verified, then adversarially re-checked — no barrier, so
// an item is re-checked as soon as its verification lands.
const audited = await pipeline(
  ITEMS,
  (it, _o, i) => agent(
    `Ground-truth the staleness of this Mercury external dependency and classify it.\n\nItem: ${it.key}\n${it.claim}\nMercury refs: ${it.refs || '(none)'}\n\n` +
    `Read/Grep the cited Mercury files yourself for what is pinned/used. Use WebSearch/WebFetch to find the CURRENT upstream state (latest npm/PyPI/crates/GitHub release, repo alive/archived, model availability). ` +
    `Classify: ACTIVE-RISK (stale/dead component is actively used and can cause a real failure NOW) · ACTION-NEEDED (behind upstream / re-pin or pull-update warranted, not yet failing) · ACCEPTABLE-DRIFT (upstream changed but Mercury owns an adapted copy / change is cosmetic or unreachable — no action) · DORMANT-OK (defensive/unused reference, safe to leave) · NOT-STALE (current) · UNVERIFIED (could not confirm upstream state). ` +
    `State the concrete impact (local problem from outdated OR wrong use of a dead component) and a recommended action. Cite source URLs.`,
    { label: `verify:${i + 1}:${(it.key || '').slice(0, 18)}`, phase: 'Verify', schema: VERIFY_SCHEMA, agentType: 'research' }
  ).then(v => ({ ...it, verify: v })),
  (iv, _o, i) => {
    if (!iv || !iv.verify) return iv
    return agent(
      `Adversarially re-check a staleness classification. Be skeptical — challenge both false-alarms and missed-risks.\n\n` +
      `Item: ${iv.key}\n${iv.claim}\nOriginal classification: ${iv.verify.classification} — ${iv.verify.verdict}\nClaimed current upstream: ${iv.verify.currentUpstream || '?'}\nClaimed impact: ${iv.verify.impact}\n\n` +
      `Independently re-verify the upstream state via WebSearch/WebFetch (do not trust the original verdict). Could a DORMANT-OK / ACCEPTABLE-DRIFT / NOT-STALE actually hide an ACTIVE-RISK (a disabled model still routed, an archived upstream, a security CVE in the lagging version)? Could an ACTIVE-RISK / ACTION-NEEDED be overstated? ` +
      `Return whether you agree, a revised classification (or UNCHANGED), concerns, and your reason.`,
      { label: `adv:${i + 1}:${(iv.key || '').slice(0, 18)}`, phase: 'Adversarial', schema: ADV_SCHEMA, agentType: 'research' }
    ).then(a => ({ ...iv, adv: a }))
  }
)

phase('Synthesize')
const A = audited.filter(Boolean)
const finalClass = r => {
  const orig = r.verify && r.verify.classification
  const rev = r.adv && r.adv.revisedClassification
  return (rev && rev !== 'UNCHANGED') ? rev : orig
}
const shape = r => ({
  key: r.key,
  classification: finalClass(r),
  originalClassification: r.verify && r.verify.classification,
  skepticAgreed: r.adv ? r.adv.agree : null,
  verdict: r.verify && r.verify.verdict,
  currentUpstream: r.verify && r.verify.currentUpstream,
  pinnedOrUsed: r.verify && r.verify.pinnedOrUsed,
  impact: r.verify && r.verify.impact,
  recommendedAction: r.verify && r.verify.recommendedAction,
  concerns: (r.adv && r.adv.concerns) || [],
  sources: (r.verify && r.verify.sources) || [],
})
const all = A.map(shape)
const bucket = c => all.filter(x => x.classification === c)
log(`Audit: ${bucket('ACTIVE-RISK').length} ACTIVE-RISK · ${bucket('ACTION-NEEDED').length} ACTION-NEEDED · ${bucket('ACCEPTABLE-DRIFT').length} ACCEPTABLE-DRIFT · ${bucket('DORMANT-OK').length} DORMANT-OK · ${bucket('NOT-STALE').length} NOT-STALE · ${bucket('UNVERIFIED').length} UNVERIFIED`)
return {
  itemCount: all.length,
  activeRisk: bucket('ACTIVE-RISK'),
  actionNeeded: bucket('ACTION-NEEDED'),
  acceptableDrift: bucket('ACCEPTABLE-DRIFT'),
  dormantOk: bucket('DORMANT-OK'),
  notStale: bucket('NOT-STALE'),
  unverified: bucket('UNVERIFIED'),
  note: 'Classifications where skepticAgreed=false were revised by the adversarial pass. UNVERIFIED items MUST be re-checked manually before any action (Mercury research protocol). This audit files NO issues — findings feed the operator triage + any follow-up Issue/PR (see .mercury/docs/guides/upstream-drift-routine.md).',
}
