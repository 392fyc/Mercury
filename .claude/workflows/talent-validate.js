export const meta = {
  name: 'talent-validate',
  description: 'Hybrid talent-balance validator for the SoT (Ship of Theseus) game design layer: deterministic structure/tag/rule checks (zero-LLM, in-script) + Haiku semantic advisory + Haiku triage of shared-tag combinations + an adversarial Optimizer-vs-Defender pass that hunts abuse sequences. Pure LLM is a known-lenient balance judge, so quantitative/structural checks are done in code and the LLM is restricted to semantics + adversarial roles. Also carries an on-demand L4 gap-fill mode (args {"gapfill": true}): builds a trigger×effect coverage matrix over the stored corpus, generates ONE schema-legal candidate for the best empty cell, embedding-screens it for redundancy, and feeds it straight through L1-L3.',
  whenToUse: 'Validating a SoT talent (by id from the Codex design API, or an inline draft) before it is locked: catches illegal schema/tags, dangling rule refs, supply-budget breaches, dangerous tag-combination loops, and high-value abuse sequences. Or, with {"gapfill": true}, proposing ONE new pre-validated design-space candidate where the corpus has no coverage. Read-only against SoT — produces a findings report; never writes to the SoT repos. Numeric power-scoring is intentionally OUT of this MVP (the Codex narrative layer has no numeric power field yet; see roadmap §1.3 / §5).',
  phases: [
    { title: 'Adapt', detail: 'pull candidate + peers + tag registry + rules from the Codex read-only API (or a prepared dataDir)' },
    { title: 'GapFill', detail: 'L4 (gapfill mode only): classify corpus onto trigger×effect axes -> pick top empty cell -> generate ONE candidate -> embedding redundancy screen' },
    { title: 'Validate', detail: 'L1 deterministic (schema/enum/tag/rule-ref/supply) in-script + Haiku semantic advisory' },
    { title: 'Combine', detail: 'L2 enumerate shared-tag peer pairs in code, Haiku-triage each interaction' },
    { title: 'Adversarial', detail: 'L3 serial Optimizer (abuse sequence) -> Defender (refute), survivors = confirmed exploits' },
    { title: 'Report', detail: 'consolidate L1/L2/L3 (+ L4 gap context) into one structured verdict' },
  ],
}

// ── Mercury guardrails (#385 context economics; see .mercury/docs/guides/fanout-and-skill-migration-guardrails.md) ──
// 1. LEAN DISPATCH: the Adapt agent writes the full corpus to `dataDir` as JSON files and
//    returns only a LIGHT index (candidate fields [small], peer id/name/rarity/tags, tag keys,
//    rule codes, rarity counts). L2/L3 agents are handed dataDir PATHS + a task and read the
//    peer bodies themselves — the 20-peer effect/trigger text is never bulk-injected.
// 2. FAN-OUT CAP: L2 shared-tag pairs are bounded by PAIR_CAP; anything dropped is log()'d.
//    L3 is a fixed 2-agent serial duel; L4 gapfill (opt-in mode) adds a fixed 3-agent serial
//    chain. Worst-case agents ≈ 1 (Adapt) + 3 (L4, gapfill mode only) + 1 (L1 semantic) +
//    PAIR_CAP (L2, <=30) + 2 (L3) = <=37 — far under the 800-agent self-budget / 1000 runtime cap.
// 3. MODEL ROUTING (roadmap §1.2): Adapt = Sonnet (single corpus-assembly call, needs solid
//    tool use); L1-semantic + L2-triage = Haiku (cheap structured calls, injected slice << 50K
//    so we stay clear of Haiku's 200K hard cliff); L3 duel = Sonnet (strong enough for
//    adversarial reasoning without paying for Opus on every pair); L4 gapfill trio = Sonnet
//    (classification judgment / constrained generation / one-shot embedding scripting).
// 4. READ-ONLY ON SoT: Adapt only does GET against the Codex API and writes fixtures under
//    Mercury's own tmp dir — it never mutates the SoT repos (CLAUDE.md hard constraint).
// 5. FAIL-CLOSED: any L2/L3 agent failure (parallel null) is tracked, surfaced, and forces the
//    verdict to at least `revise` — an incomplete adversarial pass must never read as `pass`.

// ── Inputs ──
// The runtime hands arguments through VERBATIM — a slash-command / tool call may deliver a
// STRING (either a bare talent id, or accidentally JSON-encoded args). Self-heal the
// JSON-encoded case instead of misreading the whole blob as a talent id (observed live:
// stringified {"gapfill": true, ...} ran as talent_id and burned an L3 pass on garbage).
let input = args
if (typeof input === 'string') {
  const s = input.trim()
  if (s.startsWith('{')) {
    try { input = JSON.parse(s) } catch (e) { /* not JSON — treated as a bare id below */ }
  }
}
// A bare-string id must LOOK like an id; anything else is a malformed invocation, not an id.
const bareId = (typeof input === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(input.trim())) ? input.trim() : null
if (typeof input === 'string' && !bareId) {
  log(`Malformed string argument (not a talent id, not parseable JSON): ${input.slice(0, 120)}`)
  return { talentId: null, verdict: 'blocked', error: 'malformed args — pass {"talent_id": "..."}, {"talent_draft_json": {...}}, {"gapfill": true}, or a bare talent id string' }
}
// All option reads below go through `opts` so the self-healed object is the single source.
const opts = (input && typeof input === 'object' && !Array.isArray(input)) ? input : {}
const talentId = opts.talent_id || opts.talentId || bareId
const draft = opts.talent_draft_json || opts.draft || null
// L4 gap-fill mode (roadmap §1.2 L4 / §9.4): no candidate input — the workflow finds an
// uncovered trigger×effect cell in the stored corpus, generates ONE schema-legal candidate
// for it (batch generation is deliberately unsupported: triage burden > value), screens it
// for embedding redundancy, then validates it through L1-L3 like any draft.
let gapfill = !!(opts.gapfill || opts.gap_fill)
if (gapfill && (talentId || draft)) {
  log('NOTE: gapfill=true ignored — an explicit talent_id/draft was also provided and takes precedence')
  gapfill = false
}
if (!talentId && !draft && !gapfill) {
  log('No talent specified. Pass {"talent_id": "..."} or {"talent_draft_json": {...}} to validate, or {"gapfill": true} to generate one gap-filling candidate.')
  return { talentId: null, verdict: 'blocked', error: 'missing talent_id / talent_draft_json / gapfill' }
}
const classId = opts.class_id || opts.classId || (draft && draft.class_id) || 'ss'
const codexBaseUrl = opts.codex_base_url || opts.codexBaseUrl || 'http://127.0.0.1:8000'
// Prepared fixtures dir (skips the API round-trip when supplied). The relative default
// resolves against the repo root (agent cwd); the Adapt agent echoes back the absolute
// path it actually used, and that absolute path is what L2/L3 receive (corpusDir).
const dataDir = opts.dataDir || '.mercury/tmp/codex-fixtures'
// L2 fan-out cap: how many shared-tag peer pairs get a triage agent. Clamped [1, ceiling];
// overflow is log()'d, never silently dropped. Default 20 covers the dense ss corpus
// (20 same-class talents share popular tags like 攻击/战棋); raise pairCap for full coverage.
const PAIR_CAP = Math.max(1, Math.min(opts.pairCap || 20, 30))
// R6.6 supply budget — HARD-COUPLED to the SoT rule registry: the code 'R6.6' and the epic
// (史诗) cap of 6 mirror the locked rule text as of 2026-07. If SoT renumbers the rule or
// changes the cap, update BOTH constants (the check silently disables when the code is
// absent/unlocked — by design — so a renamed rule would also silently disable it).
const EPIC_SUPPLY_RULE = 'R6.6'
const EPIC_SUPPLY_CAP = 6
// Set refresh=true to force the Adapt agent to re-fetch everything from the API even when
// dataDir already holds fixture files (the reuse default can silently serve a stale snapshot
// after the design library has been edited).
const refreshData = !!(opts.refresh || opts.refreshData)

// ── L4 gap-fill config (only read in gapfill mode) ──
// Embedding redundancy screen: NO hardcoded resource URL — pass embed_base_url via args, or
// let the screening agent probe env AZURE_OPENAI_EMBED_BASE_URL then OPENAI_BASE_URL. The key
// comes from env (AZURE_OPENAI_API_KEY, fallback OPENAI_API_KEY) read by the agent, never
// inlined here. Missing/failed embedding config fails CLOSED: similarity marked UNVERIFIED
// and the verdict is forced to at least revise.
const embedBaseUrl = opts.embed_base_url || opts.embedBaseUrl || null
const embedModel = opts.embed_model || opts.embedModel || 'text-embedding-3-small'
const EMBED_REDUNDANCY_COS = 0.85
// Matrix axes: in-script defaults derived from the SoT tag registry + corpus conventions,
// overridable via args.triggerAxes / args.effectAxes. A miscategorized axis cannot corrupt
// validation (L1-L3 are unaffected) — it only shapes WHERE the ideation looks. The catch-all
// bucket surfaces vocabulary drift and is never treated as a design target.
const GAP_CATCH_ALL = '其他'
const DEFAULT_TRIGGER_AXES = ['主动使用', '攻击/命中时', '被攻击/受击时', '击杀/处决时', '回合开始/结束', '移动/位移时', '资源变化时', GAP_CATCH_ALL]
const DEFAULT_EFFECT_AXES = ['直接伤害', '增伤/暴击强化', '防御/减伤/招架', '控制/限制', '位移/机动', '资源获取', '状态施加/印记', '连击/追击', GAP_CATCH_ALL]

// Operator-supplied inputs still get sanity bounds before being interpolated into agent
// prompts: the base URL must be a plain http(s) URL without embedded credentials/whitespace
// (deliberately NOT localhost-restricted — the design-library API is planned to move behind
// the NAS URL once a CF service token exists, see the usage guide), and dataDir must not
// traverse upward out of the workspace.
// Plain http(s) URL from a conservative charset: host[:port][/path] only — no userinfo,
// quotes, backticks, spaces, or other shell/prompt-breaking characters can survive this shape.
const SAFE_URL_RE = /^https?:\/\/[A-Za-z0-9.-]+(:\d+)?(\/[A-Za-z0-9._~/-]*)?$/
if (typeof codexBaseUrl !== 'string' || !SAFE_URL_RE.test(codexBaseUrl)) {
  log(`Blocked unsafe codex_base_url: ${codexBaseUrl}`)
  return { talentId: talentId || (draft && draft.id) || null, verdict: 'blocked', error: 'unsafe codex_base_url (must be a plain http(s) host[:port][/path] URL)' }
}
// dataDir: no upward traversal, and a conservative path charset — newlines, quotes, backticks,
// '$' and friends must not ride into agent-executed Bash/python. Absolute paths stay ALLOWED:
// prepared fixtures may live anywhere the operator chooses (ADAPT_SCHEMA.dataDir is documented
// as an absolute dir), so this is a charset bound, not a root lock.
if (typeof dataDir !== 'string' || dataDir.includes('..') || !/^[A-Za-z0-9._:/\\ -]{1,200}$/.test(dataDir)) {
  log('Blocked unsafe dataDir (traversal or unsafe characters)')
  return { talentId: talentId || (draft && draft.id) || null, verdict: 'blocked', error: 'unsafe dataDir (no .., path-safe characters only)' }
}
if (embedBaseUrl !== null && (typeof embedBaseUrl !== 'string' || !SAFE_URL_RE.test(embedBaseUrl))) {
  log(`Blocked unsafe embed_base_url: ${embedBaseUrl}`)
  return { talentId: null, verdict: 'blocked', error: 'unsafe embed_base_url (must be a plain http(s) host[:port][/path] URL)' }
}
// classId rides into file paths (talents_<classId>.json), agent-executed curl URLs, and the
// generated id prefix — same sanity bound as its sibling inputs.
if (typeof classId !== 'string' || !/^[A-Za-z0-9_-]{1,32}$/.test(classId)) {
  log(`Blocked unsafe class_id: ${classId}`)
  return { talentId: null, verdict: 'blocked', error: 'unsafe class_id (must match [A-Za-z0-9_-]{1,32})' }
}

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

// ── L4 gap-fill schemas ──
// Adapt variant without a candidate: gapfill has no talent under test yet.
const GAP_ADAPT_SCHEMA = {
  type: 'object', required: ['peers', 'tagKeys', 'ruleCodes', 'rarityCounts', 'dataDir'],
  properties: {
    peers: {
      type: 'array', description: 'ALL stored same-class talents, LIGHT index only (no effect bodies)',
      items: {
        type: 'object', required: ['id', 'name', 'tags'],
        properties: { id: { type: 'string' }, name: { type: 'string' }, rarity: { type: 'string' }, tags: { type: 'array', items: { type: 'string' } } },
      },
    },
    tagKeys: { type: 'array', items: { type: 'object', required: ['key', 'layer'], properties: { key: { type: 'string' }, layer: { type: 'string', enum: ['mech', 'content'] } } } },
    ruleCodes: { type: 'array', items: { type: 'object', required: ['code', 'status'], properties: { code: { type: 'string' }, status: { type: 'string' }, group: { type: 'string' } } } },
    rarityCounts: { type: 'object', additionalProperties: { type: 'number' } },
    dataDir: { type: 'string', description: 'absolute dir holding talents_<class>.json / tags.json / rules.json' },
  },
}
const GAP_CLASSIFY_SCHEMA = {
  type: 'object', required: ['classified'],
  properties: {
    classified: {
      type: 'array', description: 'one entry per stored talent — EXACTLY one bucket per axis',
      items: { type: 'object', required: ['id', 'trigger_cat', 'effect_cat'], properties: {
        id: { type: 'string' }, trigger_cat: { type: 'string' }, effect_cat: { type: 'string' },
      } },
    },
  },
}
const GAP_GENERATE_SCHEMA = {
  type: 'object', required: ['candidate', 'design_rationale'],
  properties: {
    candidate: {
      type: 'object', required: ['id', 'class_id', 'name', 'damage_type', 'rarity', 'status', 'trigger', 'effect', 'rules', 'tags'],
      properties: {
        id: { type: 'string' }, class_id: { type: 'string' }, name: { type: 'string' },
        damage_type: { type: 'string' }, rarity: { type: 'string' }, status: { type: 'string' },
        trigger: { type: 'string' }, effect: { type: 'string' }, rules: { type: 'string' },
        tags: { type: 'array', items: { type: 'string' } },
      },
    },
    design_rationale: { type: 'string', description: 'why this fills the target cell; cite the corpus talents used for power calibration' },
  },
}
const GAP_EMBED_SCHEMA = {
  type: 'object', required: ['status', 'max_cos', 'most_similar_id'],
  properties: {
    status: { type: 'string', enum: ['ok', 'unavailable'], description: 'unavailable = endpoint/key missing or the embeddings call failed; NEVER fabricate numbers' },
    max_cos: { type: 'number', description: 'highest cosine similarity vs any stored talent; -1 when unavailable' },
    most_similar_id: { type: 'string', description: 'stored talent id with the highest cosine; empty when unavailable' },
    detail: { type: 'string' },
  },
}

// Coverage matrix + gap ranking — pure JS (the LLM never drives the quantitative selection).
// gap-fit: prefer cells whose row AND column are each already inhabited (both axis values are
// proven viable in this class; only the combination is unexplored), then by total occupancy.
function buildGapCells(classified, tAxes, eAxes) {
  const filled = new Set(classified.map(c => `${c.trigger_cat}|${c.effect_cat}`))
  // Null-prototype maps: axis labels are user-suppliable, and a label like "__proto__" on a
  // plain object would corrupt the counts (prototype pollution).
  const rowCount = Object.create(null), colCount = Object.create(null)
  for (const c of classified) {
    rowCount[c.trigger_cat] = (rowCount[c.trigger_cat] || 0) + 1
    colCount[c.effect_cat] = (colCount[c.effect_cat] || 0) + 1
  }
  const cells = []
  for (const t of tAxes) {
    for (const e of eAxes) {
      if (t === GAP_CATCH_ALL || e === GAP_CATCH_ALL) continue // catch-all buckets are not design targets
      if (!filled.has(`${t}|${e}`)) {
        cells.push({ trigger: t, effect: e, rowN: rowCount[t] || 0, colN: colCount[e] || 0 })
      }
    }
  }
  return cells.sort((a, b) => (Math.min(b.rowN, b.colN) - Math.min(a.rowN, a.colN)) || ((b.rowN + b.colN) - (a.rowN + a.colN)))
}

// Tracks stage agents that failed (parallel null / missing result) so the verdict can fail
// closed instead of silently reading incomplete coverage as a clean pass.
const stageFailures = []

// ── Phase 0: Adapt — Codex read-only API -> light index (lean dispatch) ──
phase('Adapt')
const candId = gapfill ? 'GAPFILL' : (talentId || (draft && draft.id) || 'DRAFT')
const adapt = await agent(
  `You are a READ-ONLY data adapter for the SoT Codex design API. Do NOT write to any SoT repo.\n` +
  (gapfill
    ? `Goal: assemble the stored corpus for class_id="${classId}" (gap-fill mode — there is NO candidate talent yet).\n\n`
    : `Goal: assemble the corpus for validating talent "${candId}" (class_id="${classId}").\n\n`) +
  `Step 1 — obtain data into dataDir="${dataDir}" (create it if missing):\n` +
  (refreshData
    ? `  - REFRESH MODE: ignore any existing files in dataDir — fetch fresh tags.json, rules.json, talents_${classId}.json via Bash curl from ${codexBaseUrl} (GET /api/tags , GET /api/rules , GET /api/talents?class_id=${classId}) and overwrite them.\n`
    : `  - tags.json, rules.json, talents_${classId}.json: if present in dataDir read them, else fetch via Bash curl from ${codexBaseUrl}: GET /api/tags , GET /api/rules , GET /api/talents?class_id=${classId} and save each.\n`) +
  (gapfill
    ? ''
    : (draft
        ? `  - The candidate is an INLINE DRAFT (id "${candId}" is NOT in the API — do NOT GET it, a fetch would 404). Use this JSON VERBATIM as the candidate, and WRITE exactly this JSON to dataDir/talent_${candId}.json (overwrite any 404 placeholder): ${JSON.stringify(draft)}\n`
        : (refreshData
            ? `  - candidate: fetch fresh via GET /api/talents/${talentId} from ${codexBaseUrl} (ignore any existing dataDir copy) and save to dataDir/talent_${talentId}.json.\n`
            : `  - candidate: GET /api/talents/${talentId} from ${codexBaseUrl} (or read dataDir/talent_${talentId}.json if present) and ensure it is saved to dataDir/talent_${talentId}.json.\n`))) +
  `Step 2 — return a LIGHT index (NOT the full peer bodies):\n` +
  (gapfill ? '' : `  - candidate: the full record of the talent under test (all fields).\n`) +
  `  - peers: ${gapfill ? 'ALL stored ' : ''}same-class talents as id/name/rarity/tags ONLY (omit effect/trigger/rules bodies — they stay in the files).\n` +
  `  - tagKeys: every tag as {key, layer}. ruleCodes: every rule as {code, status, group}.\n` +
  `  - rarityCounts: count the stored same-class talents (talents_${classId}.json) by rarity.\n` +
  (gapfill ? '' : `  - candidateStoredRarity: the candidate id's rarity AS CURRENTLY STORED in talents_${classId}.json; return '' (empty string) if the id is not in that list (inline draft or brand-new id).\n`) +
  `  - dataDir: the absolute path you used.\n` +
  `Use python or jq for robust JSON parsing (the data is UTF-8 Chinese). Keep the response to the index only.`,
  { phase: 'Adapt', schema: gapfill ? GAP_ADAPT_SCHEMA : ADAPT_SCHEMA, model: 'sonnet', label: `adapt:${candId}` }
)
if (!adapt || (!gapfill && !adapt.candidate)) {
  log('Adapt failed — could not load candidate/corpus from Codex API or dataDir')
  return { talentId: candId, verdict: 'blocked', error: 'adapt failed (Codex API unreachable and no usable dataDir?)' }
}
// A structurally-present but HOLLOW candidate (no name/trigger/effect at all) means the id was
// not actually found (API down + no fixture) — block early instead of letting a shell record
// burn the L2/L3 adversarial budget on nothing (observed live before this guard existed).
if (!gapfill && adapt.candidate && !(adapt.candidate.name || adapt.candidate.trigger || adapt.candidate.effect)) {
  log(`Adapt returned a hollow candidate record for "${candId}" — id not found in API or fixtures`)
  return { talentId: candId, verdict: 'blocked', error: 'candidate record hollow / not found (check talent_id, API availability, or dataDir fixtures)' }
}
// gapfill: candidate/candidateStoredRarity are assigned by the GapFill phase below.
let candidate = gapfill ? null : adapt.candidate
let candidateStoredRarity = gapfill ? '' : (adapt.candidateStoredRarity || '')
const peers = (adapt.peers || []).filter(p => p && (!candidate || p.id !== candidate.id))
const tagKeySet = new Set((adapt.tagKeys || []).map(t => t.key))
const ruleCodeSet = new Set((adapt.ruleCodes || []).map(r => r.code))
const lockedRuleCodes = new Set((adapt.ruleCodes || []).filter(r => r.status === '锁定').map(r => r.code))
const rarityCounts = adapt.rarityCounts || {}
const corpusDir = adapt.dataDir || dataDir
log(candidate
  ? `Adapt: candidate=${candidate.id} (${candidate.name}/${candidate.rarity}) · peers=${peers.length} · tags=${tagKeySet.size} · rules=${ruleCodeSet.size} · storedRarity=${candidateStoredRarity}`
  : `Adapt (gapfill): corpus=${peers.length} stored talent(s) · tags=${tagKeySet.size} · rules=${ruleCodeSet.size}`)
// Data-completeness sanity: an empty registry would turn every tag into a false "unknown tag"
// error and mis-judge the verdict, so the affected checks are SKIPPED — but skipped coverage
// must never read as a clean pass (fail-closed): record it as a stage failure -> verdict >= revise.
if (tagKeySet.size === 0) { stageFailures.push('Adapt(tag registry empty — tag-legality checks skipped)'); log('WARN: tag registry empty — L1 SKIPS tag-legality checks (fail-closed -> revise)') }
if (ruleCodeSet.size === 0) { stageFailures.push('Adapt(rule registry empty — rule-ref/supply-budget checks skipped)'); log('WARN: rule registry empty — rule-ref / supply-budget checks disabled (fail-closed -> revise)') }

// ── Phase 0.5: L4 gap-fill — coverage matrix -> generate ONE candidate -> embedding screen ──
let gapReport = null
let gapRedundant = false
if (gapfill) {
  phase('GapFill')
  // Generation needs the FULL registries (legal tags to pick from, real rule codes to cite);
  // proceeding on an empty registry would fabricate an unvalidatable candidate — hard block.
  if (tagKeySet.size === 0 || ruleCodeSet.size === 0) {
    return { talentId: null, verdict: 'blocked', error: 'gapfill needs a complete tag + rule registry (empty registry loaded — stale dataDir? API down?)' }
  }
  if (peers.length === 0) {
    return { talentId: null, verdict: 'blocked', error: 'gapfill needs a non-empty stored corpus to map coverage against' }
  }
  let tAxes = (Array.isArray(opts.triggerAxes) && opts.triggerAxes.length > 1) ? opts.triggerAxes : DEFAULT_TRIGGER_AXES
  let eAxes = (Array.isArray(opts.effectAxes) && opts.effectAxes.length > 1) ? opts.effectAxes : DEFAULT_EFFECT_AXES
  // Axis labels become matrix keys joined with '|' — a '|' inside a label would alias two
  // distinct cells, and duplicate labels would emit duplicate cells. Axis count and label
  // length are hard-bounded: they ride into every classification prompt and drive the matrix
  // enumeration, so unbounded input means context bloat and runaway cells. Reject, don't guess.
  const AXIS_MAX_COUNT = 12
  const AXIS_LABEL_MAX_LEN = 24
  const axisProblem = [...tAxes, ...eAxes].some(a => typeof a !== 'string' || !a.trim() || a.length > AXIS_LABEL_MAX_LEN || a.includes('|'))
    || tAxes.length > AXIS_MAX_COUNT || eAxes.length > AXIS_MAX_COUNT
    || new Set(tAxes).size !== tAxes.length || new Set(eAxes).size !== eAxes.length
  if (axisProblem) {
    return { talentId: null, verdict: 'blocked', error: `invalid custom axes — per axis: <=${AXIS_MAX_COUNT} unique non-empty labels, <=${AXIS_LABEL_MAX_LEN} chars each, no "|"` }
  }
  // The catch-all bucket is STRUCTURAL, not a design target: the classifier prompt directs
  // hard-to-place talents there and the matrix skips it, so custom axes that omit it would
  // push the classifier into off-axis labels that reconciliation then drops (false shortfall).
  // Guarantee it exists on both axes.
  if (!tAxes.includes(GAP_CATCH_ALL)) tAxes = [...tAxes, GAP_CATCH_ALL]
  if (!eAxes.includes(GAP_CATCH_ALL)) eAxes = [...eAxes, GAP_CATCH_ALL]

  // Classify the stored corpus onto the axes — the only LLM judgment in the mapping step;
  // matrix arithmetic and cell selection stay in code (buildGapCells).
  const cls = await agent(
    `Read ${corpusDir}/talents_${classId}.json (UTF-8 Chinese; use python or jq). For EVERY stored talent, ` +
    `classify its trigger timing and its primary effect into EXACTLY one bucket each.\n` +
    `trigger buckets: ${JSON.stringify(tAxes)}\n` +
    `effect buckets: ${JSON.stringify(eAxes)}\n` +
    `Use "${GAP_CATCH_ALL}" ONLY when nothing else fits. Return one entry per talent id — no omissions.`,
    { phase: 'GapFill', schema: GAP_CLASSIFY_SCHEMA, model: 'sonnet', label: `gap-classify:${classId}` }
  )
  if (!cls || !Array.isArray(cls.classified) || cls.classified.length === 0) {
    return { talentId: null, verdict: 'blocked', error: 'gapfill classification failed — no coverage matrix' }
  }
  // Deterministic reconciliation against the stored corpus — the classifier is an LLM and may
  // omit, duplicate, or invent ids; none of that may silently shape the matrix. Alien ids and
  // duplicates are dropped in code (the schema cannot enforce dynamic enums or uniqueness),
  // and ANY coverage shortfall floors the verdict to revise (fail-closed): a phantom-empty
  // cell must never read as a clean novel gap.
  const legalT = new Set(tAxes), legalE = new Set(eAxes)
  const peerIdSet = new Set(peers.map(p => p.id))
  const classifiedIds = new Set()
  const classified = []
  let alienN = 0, dupN = 0, offAxisN = 0
  for (const c of cls.classified) {
    if (!peerIdSet.has(c.id)) { alienN += 1; continue }
    if (classifiedIds.has(c.id)) { dupN += 1; continue }
    if (!legalT.has(c.trigger_cat) || !legalE.has(c.effect_cat)) { offAxisN += 1; continue }
    classifiedIds.add(c.id)
    classified.push(c)
  }
  if (alienN > 0) log(`WARN: ${alienN} classification(s) referenced ids not in the corpus and were dropped`)
  if (dupN > 0) log(`WARN: ${dupN} duplicate classification(s) dropped (first occurrence kept)`)
  if (offAxisN > 0) log(`WARN: ${offAxisN} classification(s) used off-axis labels and were dropped`)
  if (classified.length === 0) {
    return { talentId: null, verdict: 'blocked', error: 'gapfill classification unusable — nothing survived reconciliation (alien / duplicate / off-axis)' }
  }
  if (classified.length < peers.length) {
    stageFailures.push(`L4(coverage matrix incomplete — ${classified.length}/${peers.length} stored talents classified)`)
    log(`WARN: coverage matrix built from ${classified.length}/${peers.length} stored talents — possible phantom gaps (fail-closed -> revise)`)
  }

  const gapCells = buildGapCells(classified, tAxes, eAxes)
  if (gapCells.length === 0) {
    log('GapFill: matrix saturated — every non-catch-all trigger×effect cell is already covered')
    return { talentId: null, verdict: 'blocked', error: 'gap matrix saturated — no empty cell to fill (nothing to generate)', gapfill: { saturated: true, targetCell: null, classifiedCount: classified.length, corpusCount: peers.length } }
  }
  const targetCell = gapCells[0]
  log(`GapFill: ${gapCells.length} empty cell(s) · target =「${targetCell.trigger} × ${targetCell.effect}」(row ${targetCell.rowN} / col ${targetCell.colN} occupied) · runners-up: ${gapCells.slice(1, 3).map(c => `${c.trigger}×${c.effect}`).join(' , ') || 'none'}`)

  // Generate exactly ONE candidate (roadmap §1.2 L4: batch generation is deliberately
  // unsupported — triage burden > value; runner-up cells are reported, not generated).
  const legalTags = (adapt.tagKeys || []).map(t => t.key)
  const gen = await agent(
    `You design ONE new talent for the SoT tactical RPG (class_id="${classId}") that fills an uncovered design-space cell.\n` +
    `Target cell: trigger timing =「${targetCell.trigger}」, primary effect =「${targetCell.effect}」.\n` +
    `Read ${corpusDir}/talents_${classId}.json for wording style + power calibration, and ${corpusDir}/rules.json for the rule registry — cite only EXISTING rule codes in the candidate's "rules" field.\n` +
    `Hard constraints: tags MUST be chosen from ${JSON.stringify(legalTags)}; rarity ∈ 普通/稀有/史诗/传奇 (stored counts: ${JSON.stringify(rarityCounts)} — avoid 史诗 unless the design demands it); damage_type ∈ 无/物理/魔法/圣/纯伤害/混合; status = "草稿"; id = "${classId}_gap_" + a short pinyin slug of the name, colliding with NO stored id; class_id = "${classId}".\n` +
    `Power level: comparable to same-rarity stored talents — do not exceed them. Return the candidate + a design_rationale citing the corpus talents you calibrated against.`,
    { phase: 'GapFill', schema: GAP_GENERATE_SCHEMA, model: 'sonnet', label: `gap-generate:${targetCell.trigger}×${targetCell.effect}` }
  )
  if (!gen || !gen.candidate || typeof gen.candidate !== 'object' || Array.isArray(gen.candidate) || !gen.candidate.id) {
    return { talentId: null, verdict: 'blocked', error: 'gapfill generation failed — no usable candidate object produced' }
  }
  candidate = gen.candidate
  candidate.class_id = classId
  candidate.status = '草稿' // a generated candidate must never claim any other lifecycle state
  // Deterministic id hygiene — identity invariants are never left to the generator: enforce
  // the gap prefix and de-collide against stored ids (a reused id would poison the R6.6
  // "newly added" accounting below and make L2 compare the candidate against itself).
  let genId = String(candidate.id).trim().replace(/\s+/g, '_').replace(/[^A-Za-z0-9_-]/g, '_')
  if (!genId.startsWith(`${classId}_gap_`)) genId = `${classId}_gap_${genId.replace(new RegExp(`^${classId}_`), '')}`
  genId = genId.slice(0, 56) // same safe-id charset as bare-id parsing; leave room for suffixes
  while (peerIdSet.has(genId)) genId += '_x'
  if (genId !== candidate.id) log(`GapFill: candidate id normalized「${candidate.id}」->「${genId}」`)
  candidate.id = genId
  candidateStoredRarity = '' // brand-new (now guaranteed-unique) id: R6.6 treats it as newly added

  // Embedding redundancy screen — one agent does the whole vector step via Bash python and
  // returns ONLY the summary (never the raw vectors: 20×1536 floats would be bulk injection).
  // Candidate text is generator output — strip shell/prompt-breaking characters and cap the
  // length before it rides into a prompt that drives Bash/python (defense-in-depth: JSON
  // escaping alone doesn't stop an agent from pasting the text into a shell command).
  const sanitizeEmbedText = (v, max) => String(v || '').replace(/[`$\\]/g, '').replace(/\r?\n/g, ' ').slice(0, max)
  const embCandidate = {
    name: sanitizeEmbedText(candidate.name, 80),
    trigger: sanitizeEmbedText(candidate.trigger, 300),
    effect: sanitizeEmbedText(candidate.effect, 300),
  }
  const emb = await agent(
    `Compute embedding cosine similarity between ONE candidate talent and every stored talent, via Bash + python.\n` +
    `Endpoint: ${embedBaseUrl ? `use base URL "${embedBaseUrl}"` : 'read env AZURE_OPENAI_EMBED_BASE_URL, else env OPENAI_BASE_URL'} — POST <base>/embeddings with header "api-key" from env AZURE_OPENAI_API_KEY (fallback: env OPENAI_API_KEY as "Authorization: Bearer"). NEVER print any key.\n` +
    `Model: "${embedModel}". Text per talent = name + "|" + trigger + "|" + effect.\n` +
    `Candidate (verbatim): ${JSON.stringify(embCandidate)}\n` +
    `Stored talents: read ${corpusDir}/talents_${classId}.json. Batch all texts in ONE embeddings request (candidate first), then cosine in python.\n` +
    `Return status="ok" with max_cos + most_similar_id, or status="unavailable" with max_cos=-1 if the endpoint/key is missing or ANY call fails — never fabricate numbers.`,
    { phase: 'GapFill', schema: GAP_EMBED_SCHEMA, model: 'sonnet', label: `gap-embed:${candidate.id}` }
  )
  if (!emb || emb.status !== 'ok' || typeof emb.max_cos !== 'number' || emb.max_cos < -1 || emb.max_cos > 1) {
    // FAIL-CLOSED: an unscreened candidate must not read as clean.
    stageFailures.push('L4(embedding screen unavailable — redundancy UNVERIFIED)')
    log(`WARN: embedding screen unavailable (${(emb && emb.detail) || 'agent failed'}) — redundancy UNVERIFIED (fail-closed -> revise)`)
  } else if (emb.max_cos > EMBED_REDUNDANCY_COS) {
    gapRedundant = true
    log(`GapFill: candidate is REDUNDANT — cos ${emb.max_cos.toFixed(3)} vs ${emb.most_similar_id} > ${EMBED_REDUNDANCY_COS} (verdict floor: revise)`)
  } else {
    log(`GapFill: redundancy screen ok — max cos ${emb.max_cos.toFixed(3)} vs ${emb.most_similar_id}`)
  }

  gapReport = {
    targetCell,
    gapCellsTop3: gapCells.slice(0, 3),
    axes: { trigger: tAxes, effect: eAxes },
    classifiedCount: classified.length,
    corpusCount: peers.length,
    redundancy: (emb && emb.status === 'ok') ? { max_cos: emb.max_cos, most_similar_id: emb.most_similar_id, threshold: EMBED_REDUNDANCY_COS, redundant: gapRedundant } : { status: 'UNVERIFIED' },
    design_rationale: gen.design_rationale || '',
  }
  log(`GapFill: generated candidate ${candidate.id}「${candidate.name}」(${candidate.rarity}) — feeding into L1-L3`)
}

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
    } else if (candidate.rarity === '史诗') {
      // Registry loaded but the supply rule is absent or not locked — surface the skipped
      // check loudly instead of letting registry drift disable it in silence.
      log(`NOTE: epic-supply check skipped — ${EPIC_SUPPLY_RULE} is absent or not 锁定 in the loaded rule registry (registry drift / renamed rule?)`)
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
else if (l1Warnings > 0 || l2Flagged.length > 0 || l3.undetermined || stageFailures.length > 0 || gapRedundant) verdict = 'revise'
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
  gapfill: gapReport, // null unless {"gapfill": true} mode; holds target cell + redundancy screen
  candidateDraft: gapReport ? candidate : undefined, // the full generated draft for the designer
  corpusDir,
}
