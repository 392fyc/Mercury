export const meta = {
  name: 'talent-validate',
  description: 'Hybrid talent-balance validator for the SoT (Ship of Theseus) game design layer: deterministic structure/tag/rule checks (zero-LLM, in-script) + Haiku semantic advisory + Haiku triage of shared-tag combinations + an adversarial Optimizer-vs-Defender pass that hunts abuse sequences. Pure LLM is a known-lenient balance judge, so quantitative/structural checks are done in code and the LLM is restricted to semantics + adversarial roles.',
  whenToUse: 'Validating a SoT talent (by id from the Codex design API, or an inline draft) before it is locked: catches illegal schema/tags, dangling rule refs, supply-budget breaches, dangerous tag-combination loops, and high-value abuse sequences. Read-only against SoT — produces a findings report; never writes to the SoT repos. Numeric power-scoring is intentionally OUT of this MVP (the Codex narrative layer has no numeric power field yet; see roadmap §1.3 / §5).',
  phases: [
    { title: 'Adapt', detail: 'pull candidate + peers + tag registry + rules from the Codex read-only API (or a prepared dataDir)' },
    { title: 'Validate', detail: 'L1 deterministic (schema/enum/tag/rule-ref/supply) in-script + Haiku semantic advisory' },
    { title: 'Combine', detail: 'L2 enumerate shared-tag peer pairs in code, Haiku-triage each interaction' },
    { title: 'Adversarial', detail: 'L3 serial Optimizer (abuse sequence) -> Defender (refute), survivors = confirmed exploits' },
    { title: 'Report', detail: 'consolidate L1/L2/L3 into one structured verdict' },
  ],
}

// ── Mercury guardrails (#385 context economics; see .mercury/docs/guides/fanout-and-skill-migration-guardrails.md) ──
// 1. LEAN DISPATCH: the Adapt agent writes the full corpus to `dataDir` as JSON files and
//    returns only a LIGHT index (candidate fields [small], peer id/name/rarity/tags, tag keys,
//    rule codes, rarity counts). L2/L3 agents are handed dataDir PATHS + a task and read the
//    peer bodies themselves — the 20-peer effect/trigger text is never bulk-injected.
// 2. FAN-OUT CAP: L2 shared-tag pairs are bounded by PAIR_CAP; anything dropped is log()'d.
//    L3 is a fixed 2-agent serial duel. Worst-case agents ≈ 1 (Adapt) + 1 (L1 semantic) +
//    PAIR_CAP (L2, <=30) + 2 (L3) = <=34 — far under the 800-agent self-budget / 1000 runtime cap.
// 3. MODEL ROUTING (roadmap §1.2): Adapt = Sonnet (single corpus-assembly call, needs solid
//    tool use); L1-semantic + L2-triage = Haiku (cheap structured calls, injected slice << 50K
//    so we stay clear of Haiku's 200K hard cliff); L3 duel = Sonnet (strong enough for
//    adversarial reasoning without paying for Opus on every pair).
// 4. READ-ONLY ON SoT: Adapt only does GET against the Codex API and writes fixtures under
//    Mercury's own tmp dir — it never mutates the SoT repos (CLAUDE.md hard constraint).
// 5. FAIL-CLOSED: any L2/L3 agent failure (parallel null) is tracked, surfaced, and forces the
//    verdict to at least `revise` — an incomplete adversarial pass must never read as `pass`.

// ── Inputs ──
const talentId = (args && (args.talent_id || args.talentId)) || (typeof args === 'string' ? args : null)
const draft = (args && (args.talent_draft_json || args.draft)) || null
if (!talentId && !draft) {
  log('No talent specified. Pass one via args, e.g. /talent-validate { "talent_id": "ss_jianqie" } or { "talent_draft_json": {...} }')
  return { talentId: null, verdict: 'blocked', error: 'missing talent_id or talent_draft_json' }
}
const classId = (args && (args.class_id || args.classId)) || (draft && draft.class_id) || 'ss'
const codexBaseUrl = (args && (args.codex_base_url || args.codexBaseUrl)) || 'http://127.0.0.1:8000'
// Prepared fixtures dir (skips the API round-trip when supplied). The relative default
// resolves against the repo root (agent cwd); the Adapt agent echoes back the absolute
// path it actually used, and that absolute path is what L2/L3 receive (corpusDir).
const dataDir = (args && args.dataDir) || '.mercury/tmp/codex-fixtures'
// L2 fan-out cap: how many shared-tag peer pairs get a triage agent. Clamped [1, ceiling];
// overflow is log()'d, never silently dropped. Default 20 covers the dense ss corpus
// (20 same-class talents share popular tags like 攻击/战棋); raise pairCap for full coverage.
const PAIR_CAP = Math.max(1, Math.min((args && args.pairCap) || 20, 30))
// R6.6 supply budget — HARD-COUPLED to the SoT rule registry: the code 'R6.6' and the epic
// (史诗) cap of 6 mirror the locked rule text as of 2026-07. If SoT renumbers the rule or
// changes the cap, update BOTH constants (the check silently disables when the code is
// absent/unlocked — by design — so a renamed rule would also silently disable it).
const EPIC_SUPPLY_RULE = 'R6.6'
const EPIC_SUPPLY_CAP = 6

// ── Schemas ──
const ADAPT_SCHEMA = {
  type: 'object',
  required: ['candidate', 'peers', 'tagKeys', 'ruleCodes', 'rarityCounts', 'dataDir', 'candidateStoredRarity'],
  properties: {
    // candidate.required is the MINIMAL set needed to proceed; the FULL field spec is validated
    // in L1 (runL1Deterministic). A draft missing damage_type/status/trigger/effect passes Adapt
    // and is correctly flagged by L1 — that division is intentional, not a mismatch.
    candidate: {
      type: 'object',
      description: 'the full record of the talent under test (all Codex fields)',
      required: ['id', 'name', 'rarity', 'tags'],
      properties: {
        id: { type: 'string' }, class_id: { type: 'string' }, name: { type: 'string' },
        damage_type: { type: 'string' }, rarity: { type: 'string' }, status: { type: 'string' },
        trigger: { type: 'string' }, effect: { type: 'string' }, rules: { type: 'string' },
        tags: { type: 'array', items: { type: 'string' } },
      },
    },
    peers: {
      type: 'array', description: 'same-class talents, LIGHT index only (no effect bodies)',
      items: {
        type: 'object', required: ['id', 'name', 'tags'],
        properties: { id: { type: 'string' }, name: { type: 'string' }, rarity: { type: 'string' }, tags: { type: 'array', items: { type: 'string' } } },
      },
    },
    tagKeys: { type: 'array', items: { type: 'object', required: ['key', 'layer'], properties: { key: { type: 'string' }, layer: { type: 'string', enum: ['mech', 'content'] } } } },
    // status is REQUIRED: the R6.6 supply check only fires on locked rules (status=锁定), so a
    // missing status would silently disable enforcement.
    ruleCodes: { type: 'array', items: { type: 'object', required: ['code', 'status'], properties: { code: { type: 'string' }, status: { type: 'string' }, group: { type: 'string' } } } },
    rarityCounts: { type: 'object', description: 'same-class talent counts by rarity over the stored library, e.g. {"史诗": 7}', additionalProperties: { type: 'number' } },
    // The candidate's CURRENTLY STORED rarity in the library, or null if it is not in the library
    // (inline draft, or a brand-new id). Drives correct R6.6 accounting: a candidate that is newly
    // becoming 史诗 (storedRarity != 史诗) adds +1 to the epic pool; an already-stored epic does not.
    candidateStoredRarity: { type: 'string', description: "candidate rarity as currently stored in the library, or '' (empty string) if not stored (inline draft or brand-new id)" },
    dataDir: { type: 'string', description: 'absolute dir holding talent_<id>.json / talents_<class>.json / tags.json / rules.json for L2/L3 to read' },
  },
}
const L1_SEMANTIC_SCHEMA = {
  type: 'object', required: ['issues'],
  properties: { issues: { type: 'array', items: { type: 'object', required: ['level', 'aspect', 'message'], properties: {
    level: { type: 'string', enum: ['error', 'warning', 'note'] },
    aspect: { type: 'string', description: 'rule-consistency | effect-trigger-coherence | scope-creep | wording' },
    message: { type: 'string', description: 'concrete, cites the rule code / field; no praise' },
  } } } },
}
const L2_TRIAGE_SCHEMA = {
  type: 'object', required: ['interaction_type', 'risk_level', 'rationale'],
  properties: {
    interaction_type: { type: 'string', enum: ['loop', 'amplifier', 'neutral', 'anti-synergy'] },
    risk_level: { type: 'string', enum: ['high', 'medium', 'low', 'none'] },
    example_sequence: { type: 'string', description: 'concrete turn-by-turn if loop/amplifier, else empty' },
    mitigation: { type: 'string' },
    rationale: { type: 'string', description: 'cite the shared tag + the mechanic, not vibes' },
  },
}
const L3_OPTIMIZER_SCHEMA = {
  type: 'object', required: ['sequence', 'expected_outcome', 'win_condition_turns'],
  properties: {
    sequence: { type: 'array', items: { type: 'string' }, description: 'ordered turn-by-turn highest-value abuse line' },
    expected_outcome: { type: 'string' },
    win_condition_turns: { type: 'number', description: 'turns to reach a degenerate/winning state; lower = more dangerous' },
    assumptions: { type: 'array', items: { type: 'string' }, description: 'engine assumptions the line depends on' },
  },
}
const L3_DEFENDER_SCHEMA = {
  type: 'object', required: ['neutralized', 'mechanism'],
  properties: {
    neutralized: { type: 'boolean', description: 'true if an EXISTING locked rule already stops this line' },
    mechanism: { type: 'string', description: 'the rule code + clause that neutralizes it, or why nothing does' },
    residual_risk: { type: 'string' },
  },
}

// Tracks stage agents that failed (parallel null / missing result) so the verdict can fail
// closed instead of silently reading incomplete coverage as a clean pass.
const stageFailures = []

// ── Phase 0: Adapt — Codex read-only API -> light index (lean dispatch) ──
phase('Adapt')
const candId = talentId || (draft && draft.id) || 'DRAFT'
const adapt = await agent(
  `You are a READ-ONLY data adapter for the SoT Codex design API. Do NOT write to any SoT repo.\n` +
  `Goal: assemble the corpus for validating talent "${candId}" (class_id="${classId}").\n\n` +
  `Step 1 — obtain data into dataDir="${dataDir}" (create it if missing):\n` +
  `  - tags.json, rules.json, talents_${classId}.json: if present in dataDir read them, else fetch via Bash curl from ${codexBaseUrl}: GET /api/tags , GET /api/rules , GET /api/talents?class_id=${classId} and save each.\n` +
  (draft
    ? `  - The candidate is an INLINE DRAFT (id "${candId}" is NOT in the API — do NOT GET it, a fetch would 404). Use this JSON VERBATIM as the candidate, and WRITE exactly this JSON to dataDir/talent_${candId}.json (overwrite any 404 placeholder): ${JSON.stringify(draft)}\n`
    : `  - candidate: GET /api/talents/${talentId} from ${codexBaseUrl} (or read dataDir/talent_${talentId}.json if present) and ensure it is saved to dataDir/talent_${talentId}.json.\n`) +
  `Step 2 — return a LIGHT index (NOT the full peer bodies):\n` +
  `  - candidate: the full record of the talent under test (all fields).\n` +
  `  - peers: same-class talents as id/name/rarity/tags ONLY (omit effect/trigger/rules bodies — they stay in the files).\n` +
  `  - tagKeys: every tag as {key, layer}. ruleCodes: every rule as {code, status, group}.\n` +
  `  - rarityCounts: count the stored same-class talents (talents_${classId}.json) by rarity.\n` +
  `  - candidateStoredRarity: the candidate id's rarity AS CURRENTLY STORED in talents_${classId}.json; return '' (empty string) if the id is not in that list (inline draft or brand-new id).\n` +
  `  - dataDir: the absolute path you used.\n` +
  `Use python or jq for robust JSON parsing (the data is UTF-8 Chinese). Keep the response to the index only.`,
  { phase: 'Adapt', schema: ADAPT_SCHEMA, model: 'sonnet', label: `adapt:${candId}` }
)
if (!adapt || !adapt.candidate) {
  log('Adapt failed — could not load candidate/corpus from Codex API or dataDir')
  return { talentId: candId, verdict: 'blocked', error: 'adapt failed (Codex API unreachable and no usable dataDir?)' }
}
const candidate = adapt.candidate
const peers = (adapt.peers || []).filter(p => p && p.id !== candidate.id)
const tagKeySet = new Set((adapt.tagKeys || []).map(t => t.key))
const ruleCodeSet = new Set((adapt.ruleCodes || []).map(r => r.code))
const lockedRuleCodes = new Set((adapt.ruleCodes || []).filter(r => r.status === '锁定').map(r => r.code))
const rarityCounts = adapt.rarityCounts || {}
const candidateStoredRarity = adapt.candidateStoredRarity || ''
const corpusDir = adapt.dataDir || dataDir
log(`Adapt: candidate=${candidate.id} (${candidate.name}/${candidate.rarity}) · peers=${peers.length} · tags=${tagKeySet.size} · rules=${ruleCodeSet.size} · storedRarity=${candidateStoredRarity}`)
// Data-completeness sanity: an empty registry would turn every tag into a false "unknown tag"
// error and mis-judge the verdict, so the affected checks are SKIPPED — but skipped coverage
// must never read as a clean pass (fail-closed): record it as a stage failure -> verdict >= revise.
if (tagKeySet.size === 0) { stageFailures.push('Adapt(tag registry empty — tag-legality checks skipped)'); log('WARN: tag registry empty — L1 SKIPS tag-legality checks (fail-closed -> revise)') }
if (ruleCodeSet.size === 0) { stageFailures.push('Adapt(rule registry empty — rule-ref/supply-budget checks skipped)'); log('WARN: rule registry empty — rule-ref / supply-budget checks disabled (fail-closed -> revise)') }

// ── Phase 1: L1 deterministic (zero-LLM, in-script) ──
phase('Validate')
function runL1Deterministic() {
  const v = []
  const REQUIRED = ['id', 'class_id', 'name', 'rarity', 'damage_type', 'status', 'tags', 'trigger', 'effect']
  for (const f of REQUIRED) {
    const val = candidate[f]
    const empty = val == null || (typeof val === 'string' && !val.trim()) || (Array.isArray(val) && val.length === 0)
    if (empty) v.push({ level: 'error', check: 'schema', message: `缺失/空必填字段: ${f}` })
  }
  const ENUMS = {
    rarity: ['普通', '稀有', '史诗', '传奇'],
    damage_type: ['无', '物理', '魔法', '圣', '纯伤害', '混合'],
    status: ['草稿', '待优化', '锁定', '废弃'],
  }
  for (const [f, allowed] of Object.entries(ENUMS)) {
    if (candidate[f] != null && candidate[f] !== '' && !allowed.includes(candidate[f]))
      v.push({ level: 'error', check: 'enum', message: `${f}="${candidate[f]}" 非法 (允许: ${allowed.join('/')})` })
  }
  // tag legality — only when the registry actually loaded (else every tag would false-positive).
  if (tagKeySet.size > 0) {
    for (const tg of (candidate.tags || [])) {
      if (!tagKeySet.has(tg)) v.push({ level: 'error', check: 'tag', message: `未知 tag "${tg}" 不在注册表 (${tagKeySet.size} 个合法 key)` })
    }
  }
  if (ruleCodeSet.size > 0) {
    // Dangling rule references: any R<n>.<n>[letters] cited in rules/effect must exist in the
    // registry. Boundaries prevent matching inside larger tokens (e.g. R6.66 / XR6.6); [a-z]*
    // captures multi-letter sub-clause codes.
    const refText = `${candidate.rules || ''} ${candidate.effect || ''}`
    const refs = refText.match(/(?<![A-Za-z0-9_])R\d+\.\d+[a-z]*(?![A-Za-z0-9_])/g) || []
    for (const ref of [...new Set(refs)]) {
      if (!ruleCodeSet.has(ref)) v.push({ level: 'warning', check: 'rule-ref', message: `规则引用 ${ref} 不在规则表 (悬空引用)` })
    }
    // R6.6 supply budget: epic (史诗) cap is 6 per class. A candidate newly BECOMING epic
    // (storedRarity != 史诗) adds +1 and, if that breaches the cap, this talent CAUSES the
    // breach -> error -> reject. An already-stored epic does not change the pool; if the pool is
    // already over cap that is a pre-existing design-layer problem, not this talent's fault -> warning.
    if (candidate.rarity === '史诗' && lockedRuleCodes.has(EPIC_SUPPLY_RULE)) {
      const isNewEpic = candidateStoredRarity !== '史诗'
      let epicAfter = rarityCounts['史诗'] || 0
      if (isNewEpic) epicAfter += 1
      if (epicAfter > EPIC_SUPPLY_CAP) {
        if (isNewEpic) v.push({ level: 'error', check: 'supply-budget', message: `此候选新增史诗占位将使供给达 ${epicAfter} > ${EPIC_SUPPLY_RULE} 锁定上限 ${EPIC_SUPPLY_CAP} (该牌导致越界)` })
        else v.push({ level: 'warning', check: 'supply-budget', message: `史诗池供给 ${epicAfter} 已 > ${EPIC_SUPPLY_RULE} 上限 ${EPIC_SUPPLY_CAP} (池子既有超限,非本候选导致;需设计层腾位)` })
      }
    }
  }
  return v
}
const l1Deterministic = runL1Deterministic()
log(`L1 deterministic: ${l1Deterministic.length} finding(s) — ${l1Deterministic.filter(x => x.level === 'error').length} error / ${l1Deterministic.filter(x => x.level === 'warning').length} warning`)

// L1 semantic — ADVISORY ONLY (Haiku is a lenient judge; per this script's own design philosophy
// the LLM does not drive the quantitative verdict). Results go into the report; a failure here is
// logged but does NOT gate the verdict.
const l1Semantic = await agent(
  `Semantic check of ONE SoT talent (class_id="${classId}"). Do not praise. Only flag concrete issues.\n` +
  `Candidate (verbatim): ${JSON.stringify({ id: candidate.id, name: candidate.name, rarity: candidate.rarity, damage_type: candidate.damage_type, trigger: candidate.trigger, effect: candidate.effect, rules: candidate.rules, tags: candidate.tags })}\n` +
  `Rule registry (codes + status + group) at ${corpusDir}/rules.json — read it.\n` +
  `Check: (1) does each rule the talent CLAIMS to follow (in its 'rules' field) actually match what the 'effect' does? ` +
  `(2) does 'trigger' fit the tags (e.g. a 反应/reaction tag should trigger off an incoming event)? ` +
  `(3) scope creep — does the effect quietly do more than its rarity tier warrants? Return only issues, each citing a rule code or field.`,
  { phase: 'Validate', schema: L1_SEMANTIC_SCHEMA, model: 'haiku', effort: 'low', label: `l1-semantic:${candidate.id}` }
)
if (!l1Semantic) log('NOTE: L1 semantic agent failed (advisory only — verdict not affected)')
const l1SemanticIssues = (l1Semantic && l1Semantic.issues) || []
log(`L1 semantic (Haiku, advisory): ${l1SemanticIssues.length} issue(s)`)
// Surface error-level advisory items loudly: they do NOT gate the verdict (by design — the LLM
// never drives the quantitative ruling), so make sure a human sees them instead of them
// drowning inside the report payload.
const advisoryErrors = l1SemanticIssues.filter(i => i.level === 'error').length
if (advisoryErrors > 0) log(`NOTE: ${advisoryErrors} error-level semantic advisory item(s) — advisory only, verdict unaffected; human review recommended`)

// ── Phase 2: L2 combination scan — enumerate shared-tag pairs in code, triage in parallel ──
phase('Combine')
const candTags = new Set(candidate.tags || [])
// Enumerate (not imagine) peers sharing >=1 tag with the candidate, ranked by overlap size.
const sharedPairs = peers
  .map(p => ({ peer: p, shared: (p.tags || []).filter(t => candTags.has(t)) }))
  .filter(x => x.shared.length > 0)
  .sort((a, b) => b.shared.length - a.shared.length)
const pairsToTriage = sharedPairs.slice(0, PAIR_CAP)
if (sharedPairs.length > pairsToTriage.length) log(`L2: ${sharedPairs.length - pairsToTriage.length} shared-tag pair(s) dropped at PAIR_CAP=${PAIR_CAP} (rerun with higher pairCap to cover them)`)
log(`L2: ${pairsToTriage.length} shared-tag pair(s) to triage (of ${peers.length} peers)`)

const l2Raw = await parallel(pairsToTriage.map(({ peer, shared }) => () =>
  agent(
    `Triage the interaction between two SoT talents of class "${classId}" that share tag(s) [${shared.join(', ')}].\n` +
    `Candidate full record (verbatim): ${JSON.stringify({ id: candidate.id, name: candidate.name, rarity: candidate.rarity, trigger: candidate.trigger, effect: candidate.effect, rules: candidate.rules, tags: candidate.tags })}\n` +
    `Peer: "${peer.name}" (${peer.id}) — read its full entry inside ${corpusDir}/talents_${classId}.json and the rules in ${corpusDir}/rules.json; do NOT assume their contents.\n` +
    `Classify the interaction (loop / amplifier / neutral / anti-synergy) and risk (high/medium/low/none). ` +
    `If loop or amplifier, give a concrete turn-by-turn example_sequence and a mitigation. Cite the shared tag + the actual mechanic.`,
    { phase: 'Combine', schema: L2_TRIAGE_SCHEMA, model: 'haiku', effort: 'low', label: `l2:${candidate.id}+${peer.id}` }
  ).then(t => t && ({ peerId: peer.id, peerName: peer.name, shared, ...t }))
))
const l2Ok = l2Raw.filter(Boolean)
const l2Failed = l2Raw.length - l2Ok.length
if (l2Failed > 0) { stageFailures.push(`L2(${l2Failed}/${l2Raw.length} triage agents failed)`); log(`WARN: ${l2Failed} L2 triage agent(s) failed — coverage incomplete`) }
// Only loop/amplifier interactions that are actually risky (high/medium) count as flagged; a
// benign loop (risk none/low, e.g. mutual resource with a hard cap) must NOT inflate the verdict.
const l2Flagged = l2Ok.filter(x => (x.interaction_type === 'loop' || x.interaction_type === 'amplifier') && (x.risk_level === 'high' || x.risk_level === 'medium'))
log(`L2: ${l2Flagged.length} risky flagged interaction(s) (loop/amplifier @ high|medium) of ${l2Ok.length} triaged`)

// ── Phase 3: L3 adversarial — serial Optimizer -> Defender (Red-Teaming Game) ──
phase('Adversarial')
const optimizer = await agent(
  `You are the OPTIMIZER in an adversarial balance review of SoT (a tactical RPG). Your job: break the game with this talent.\n` +
  `Candidate talent full record (verbatim): ${JSON.stringify({ id: candidate.id, name: candidate.name, rarity: candidate.rarity, trigger: candidate.trigger, effect: candidate.effect, rules: candidate.rules, tags: candidate.tags })}\n` +
  `The ENTIRE same-class corpus + rules live at ${corpusDir} (talents_${classId}.json, rules.json) — read them; build your line from the real corpus + the candidate above, not imagination.\n` +
  `Construct the single HIGHEST-VALUE abuse sequence that combines this candidate with available same-class talents/skills. ` +
  `Return the ordered turn-by-turn sequence, the expected degenerate outcome, the turn count to reach it, and the engine assumptions it depends on. Be concrete and ruthless.`,
  { phase: 'Adversarial', schema: L3_OPTIMIZER_SCHEMA, model: 'sonnet', agentType: 'design', label: `l3-optimizer:${candidate.id}` }
)
let l3 = { abuseLine: null, defense: null, confirmedExploit: false, undetermined: false }
if (!optimizer) {
  // FAIL-CLOSED: the optimizer agent itself failed — the adversarial pass did not run at all.
  // Do NOT conflate this with "no abuse line exists"; incomplete coverage forces >= revise.
  stageFailures.push('L3(optimizer failed — adversarial pass incomplete)')
  log('WARN: L3 optimizer agent failed — adversarial coverage incomplete (fail-closed -> revise)')
} else if (!Array.isArray(optimizer.sequence) || optimizer.sequence.length === 0) {
  // The agent ran and genuinely found no abuse line — that IS a clean adversarial signal.
  log('L3: optimizer found no abuse line (clean adversarial pass)')
} else {
  const defender = await agent(
    `You are the DEFENDER in an adversarial balance review of SoT. The Optimizer claims this abuse line:\n` +
    `${JSON.stringify(optimizer)}\n` +
    `Candidate under review (verbatim): ${JSON.stringify({ id: candidate.id, name: candidate.name, rarity: candidate.rarity, effect: candidate.effect, tags: candidate.tags })}\n` +
    `Read the rules at ${corpusDir}/rules.json. Determine whether an EXISTING LOCKED rule (status=锁定) already neutralizes this line. ` +
    `Set neutralized=true ONLY if you can cite the specific rule code + clause that stops it. If nothing existing stops it, neutralized=false and describe the residual risk — that is a CONFIRMED exploit the designer must address.`,
    { phase: 'Adversarial', schema: L3_DEFENDER_SCHEMA, model: 'sonnet', agentType: 'critic', label: `l3-defender:${candidate.id}` }
  )
  if (!defender) {
    // FAIL-CLOSED: the optimizer found a line but the defender could not rule on it. Do NOT treat
    // an unrefuted-because-failed line as neutralized; mark it undetermined and force revise.
    stageFailures.push('L3(defender failed — abuse line undetermined)')
    log('WARN: L3 defender failed — abuse line UNDETERMINED, treating as unresolved risk (fail-closed)')
    l3 = { abuseLine: optimizer, defense: null, confirmedExploit: false, undetermined: true }
  } else {
    // Code-side guard on the defender's claim: neutralized=true must cite a rule code that is
    // actually in the LOCKED rule set. An uncited (or non-locked) "neutralized" is a
    // hallucination risk and is treated as UNDETERMINED (fail-closed -> revise), never as a
    // clean neutralization.
    const citedCodes = ((defender.mechanism || '').match(/(?<![A-Za-z0-9_])R\d+\.\d+[a-z]*(?![A-Za-z0-9_])/g) || [])
    const citesLockedRule = citedCodes.some(c => lockedRuleCodes.has(c))
    if (defender.neutralized === true && !citesLockedRule) {
      stageFailures.push('L3(defender claimed neutralized without citing a locked rule — undetermined)')
      log('WARN: L3 defender said neutralized but cited no LOCKED rule code — treating as UNDETERMINED (fail-closed)')
      l3 = { abuseLine: optimizer, defense: defender, confirmedExploit: false, undetermined: true }
    } else {
      l3 = { abuseLine: optimizer, defense: defender, confirmedExploit: defender.neutralized === false, undetermined: false }
      log(`L3: abuse line ${l3.confirmedExploit ? 'NOT neutralized -> CONFIRMED EXPLOIT' : 'neutralized by existing locked rule'}`)
    }
  }
}

// ── Phase 4: Report ──
phase('Report')
const l1Errors = l1Deterministic.filter(x => x.level === 'error').length
const l1Warnings = l1Deterministic.filter(x => x.level === 'warning').length
// reject: a hard structural violation, a candidate that itself causes a supply breach, or a
//   confirmed (refuted-and-survived) exploit.
// revise: softer warnings, a pre-existing pool overflow, a risky tag combination, an undetermined
//   adversarial line, OR any stage failure (incomplete coverage must not read as a clean pass).
// pass: nothing of the above.
let verdict
if (l1Errors > 0 || l3.confirmedExploit) verdict = 'reject'
else if (l1Warnings > 0 || l2Flagged.length > 0 || l3.undetermined || stageFailures.length > 0) verdict = 'revise'
else verdict = 'pass'

return {
  talentId: candidate.id,
  name: candidate.name,
  rarity: candidate.rarity,
  verdict, // pass | revise | reject | blocked
  scope: 'MVP: structural + semantic(advisory) + combinatorial + adversarial. Numeric power-scoring NOT included (Codex narrative layer; roadmap §1.3/§5).',
  stageFailures, // empty = full coverage; non-empty forced verdict to at least revise
  L1: { deterministic: l1Deterministic, semantic_advisory: l1SemanticIssues },
  L2: { triaged: l2Ok.length, failed: l2Failed, flagged: l2Flagged, droppedAtCap: Math.max(0, sharedPairs.length - pairsToTriage.length) },
  L3: l3.abuseLine ? { confirmedExploit: l3.confirmedExploit, undetermined: l3.undetermined, abuseLine: l3.abuseLine, defense: l3.defense } : { note: 'no abuse line produced' },
  corpusDir,
}
