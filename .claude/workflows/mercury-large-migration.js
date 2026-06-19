export const meta = {
  name: 'mercury-large-migration',
  description: 'Migrate many files by discovering the ones that need a change, transforming each in the working tree (one agent per file), verifying every change, and looping until none remain (loop-until-done)',
  whenToUse: 'A mechanical change applied across dozens to hundreds of files: API rename, import rewrite, config-key migration, codemod. One agent owns each file so parallel transforms never touch the same file; the loop catches files a single discovery pass misses. Edits land in the working tree — the operator reviews and commits.',
  phases: [
    { title: 'Discover', detail: 'find files still needing the migration (capped per round)' },
    { title: 'Migrate', detail: 'transform each file in the working tree, then verify' },
  ],
}

// ── Mercury guardrails (#385 + CLAUDE.md modular-design) ──
// 1. LEAN DISPATCH: each transform agent gets ONE file + the rule, and reads that file
//    itself. The migration rule is a short instruction, never a dump of every file.
// 2. BATCH_CAP bounds files-per-round; MAX_ROUNDS bounds the loop. Anything left when the
//    caps are hit is reported as `remaining`, never silently dropped.
// 3. PER-FILE OWNERSHIP, NOT WORKTREE ISOLATION: Discover returns ONE entry per file, so
//    the concurrent agents never edit the same file — there is no clobber to isolate
//    against. Edits land directly in the working tree (verifiable), and the operator
//    consolidates + commits. Worktree isolation is deliberately avoided here: the runtime
//    auto-removes an isolated worktree and its merge-back semantics are undocumented, so
//    an isolated transform could report success without its change reaching the checkout.
//    (If two sites share a file, one agent migrates all of them — see the Discover prompt.)

const rule = (args && (args.rule || args.transform)) || (typeof args === 'string' ? args : null)
const pattern = (args && args.pattern) || null
if (!rule) {
  log('No migration rule provided. Pass one via args, e.g. /mercury-large-migration { rule: "replace foo() with bar()", pattern: "src/**/*.ts" }')
  return { error: 'missing migration rule' }
}

// Caps are operator-overridable but clamped to [1, ceiling]: the upper bound keeps the #385
// fan-out guardrail under an oversized override; the lower bound stops a negative/zero value
// from silently no-op'ing the stage (a negative slice arg would behave nonsensically).
const BATCH_CAP = Math.max(1, Math.min((args && args.batchCap) || 12, 50))   // files transformed per round
// Each file spends 2 agents (migrate + verify), so the whole-run worst case is
// BATCH_CAP * MAX_ROUNDS * 2. Clamp MAX_ROUNDS so that stays under the runtime's 1000-agent
// hard cap (AGENT_BUDGET margin) — otherwise a max-configured run would be cut off mid-way
// instead of returning a clean resumable state. The loop is resumable: rerun to continue.
const AGENT_BUDGET = 800
const _maxRounds = Math.max(1, Math.min((args && args.maxRounds) || 5, 20))  // loop-until-dry safety bound
const MAX_ROUNDS = Math.max(1, Math.min(_maxRounds, Math.floor(AGENT_BUDGET / (BATCH_CAP * 2))))
if (MAX_ROUNDS < _maxRounds) log(`MAX_ROUNDS clamped ${_maxRounds}->${MAX_ROUNDS} to keep BATCH_CAP*MAX_ROUNDS*2 under the ${AGENT_BUDGET}-agent budget (rerun to continue)`)

// The script has no filesystem access, so this is a string-level guard: keep migration
// inside the repo by rejecting absolute paths (POSIX `/`, Windows drive/UNC) and any `..`
// traversal segment in a discovered path. Defense-in-depth — the agent's own tool
// allowlist is the real boundary, but a copyable template should not blindly trust paths.
function isSafeRelPath(p) {
  if (typeof p !== 'string' || !p) return false
  if (p.startsWith('/') || p.startsWith('\\') || /^[A-Za-z]:/.test(p)) return false
  return !p.replace(/\\/g, '/').split('/').some(seg => seg === '..')
}

const FILES_SCHEMA = {
  type: 'object',
  required: ['files'],
  properties: {
    files: { type: 'array', items: { type: 'object', required: ['file'], properties: { file: { type: 'string', description: 'path of a file still needing migration (one entry per file, even if it has several sites)' }, note: { type: 'string', description: 'which site(s) in the file need the change' } } } },
    remainingEstimate: { type: 'number', description: 'files known to remain beyond those returned' },
  },
}

const MIGRATE_SCHEMA = {
  type: 'object',
  required: ['status'],
  properties: {
    status: { type: 'string', enum: ['migrated', 'skipped', 'failed'] },
    detail: { type: 'string' },
  },
}

const VERIFY_SCHEMA = {
  type: 'object',
  required: ['ok'],
  properties: { ok: { type: 'boolean' }, reason: { type: 'string' } },
}

const patternHint = pattern ? ` within \`${pattern}\`` : ''
const completed = []
const completedSet = new Set()   // dedup discovered files against ones already handled
const skipped = []               // verified no-ops / already-migrated — not failures
const failures = []
const unsafeSeen = new Set()      // out-of-repo / traversal paths we refuse to touch
let round = 0
let remaining = 0
let stoppedEarly = false          // broke because a round had no actionable files (not converged)

while (round < MAX_ROUNDS) {
  round++
  phase('Discover')
  const found = await agent(
    `Find files that still need this migration${patternHint}: ${rule}. ` +
    `Use Grep/Glob to locate them. Return ONE entry per file (even if a file has several sites), only files NOT yet migrated, capped at ${BATCH_CAP}; ` +
    `set remainingEstimate to how many more files you believe exist beyond the returned list.`,
    { label: `discover:r${round}`, phase: 'Discover', schema: FILES_SCHEMA }
  )
  const rawFiles = (found && found.files) || []
  // True convergence is ONLY when Discover returns nothing at all. (A non-empty batch that
  // filters down to nothing is handled separately below — it is NOT clean convergence.)
  if (!rawFiles.length) { remaining = 0; log(`Round ${round}: Discover found no files — migration converged`); break }
  // Build the actionable set: drop unsafe/out-of-repo paths, files already handled in a
  // prior round, AND duplicates within THIS round. The within-round dedup is essential —
  // if Discover returns the same file twice, two agents would edit it in parallel and
  // clobber each other, breaking the one-agent-per-file ownership the design relies on.
  const roundSeen = new Set()
  const candidates = []
  for (const x of rawFiles) {
    if (!x || typeof x.file !== 'string' || !x.file) continue
    if (!isSafeRelPath(x.file)) { unsafeSeen.add(x.file); continue }
    if (completedSet.has(x.file) || roundSeen.has(x.file)) continue
    roundSeen.add(x.file)
    candidates.push(x)
  }
  const droppedBad = rawFiles.length - candidates.length
  if (droppedBad > 0) log(`Round ${round}: dropped ${droppedBad} discovered entries (unsafe path, duplicate, or already-migrated)`)
  const files = candidates.slice(0, BATCH_CAP)
  // Valid candidates beyond BATCH_CAP are NOT lost: they go un-migrated this round and the
  // loop re-discovers them. Fold them into `remaining` so the round cap can't exit clean
  // while files are still pending (consistent with the audit template's overflow accounting).
  const overReturned = candidates.length - files.length
  if (overReturned > 0) log(`Round ${round}: ${overReturned} valid files beyond BATCH_CAP=${BATCH_CAP} deferred to next round (counted in remaining)`)
  remaining = ((found && found.remainingEstimate) || 0) + overReturned
  if (!files.length) {
    // rawFiles was non-empty but nothing is actionable (all already-done, duplicates, or
    // unsafe). Stop to avoid an infinite loop, but do NOT claim clean convergence — the
    // residual (remainingEstimate + unsafe paths) surfaces in the return below.
    stoppedEarly = true
    log(`Round ${round}: no actionable files (all already-migrated, duplicate, or unsafe) — stopping`)
    break
  }
  log(`Round ${round}: migrating ${files.length} files (${remaining} estimated beyond this batch)`)

  // One agent owns each file (distinct files → no concurrent same-file clobber), edits in
  // the working tree, then a separate verify agent gates it. No commit here — the operator
  // reviews the consolidated diff and commits (Mercury: Main manages git).
  const results = await pipeline(
    files,
    f => agent(
      `Apply this migration to exactly one file and nothing else: ${rule}.\n` +
      `File: ${f.file}${f.note ? ` (sites: ${f.note})` : ''}\n` +
      `Read the file, migrate every site in it that the rule covers, run any scoped check available, and SAVE the edits in place. ` +
      `Do NOT commit and do NOT touch any other file.`,
      { label: `migrate:${f.file}`, phase: 'Migrate', schema: MIGRATE_SCHEMA }
    ).then(r => ({ site: f.file, result: r })),
    (prev, f) => agent(
      `Verify the migration of \`${f.file}\` is correct and complete for the rule: ${rule}. ` +
      `Read the current state of the file and confirm the change applied and nothing unrelated broke. Report ok=false with a reason if not.`,
      { label: `verify:${f.file}`, phase: 'Migrate', schema: VERIFY_SCHEMA }
    ).then(v => ({ site: f.file, migrate: prev && prev.result, verify: v }))
  )

  for (const r of results.filter(Boolean)) {
    const okVerify = r.verify && r.verify.ok
    const st = r.migrate && r.migrate.status
    // Verification is the correctness gate. A verified 'skipped' (already-migrated / no-op)
    // is NOT a failure — bucket it separately and dedup it so it isn't re-discovered.
    if (okVerify && st === 'migrated') { completed.push(r.site); completedSet.add(r.site) }
    else if (okVerify && st === 'skipped') { skipped.push(r.site); completedSet.add(r.site) }
    else failures.push({ site: r.site, reason: (r.verify && r.verify.reason) || (r.migrate && r.migrate.detail) || 'unknown' })
  }
}

// Honest residual: `remaining` carries the last round's estimate+overflow only when we hit
// the round cap or stopped early on a non-actionable batch; a clean break (Discover empty)
// reset it to 0. `converged` is true ONLY when nothing is left by any measure.
const unsafeSkipped = [...unsafeSeen]
const hitRoundCap = round >= MAX_ROUNDS && remaining > 0
const estRemaining = (hitRoundCap || stoppedEarly) ? remaining : 0
const converged = !stoppedEarly && !hitRoundCap && estRemaining === 0 && failures.length === 0 && unsafeSkipped.length === 0
if (hitRoundCap) log(`Stopped at MAX_ROUNDS=${MAX_ROUNDS} with ~${remaining} files still estimated remaining (rerun to continue)`)
if (unsafeSkipped.length) log(`${unsafeSkipped.length} discovered paths refused as unsafe/out-of-repo — never migrated (need manual handling)`)

const consolidateNote = 'Edits are in the working tree, uncommitted — review the diff and commit (Main manages git).'
let note
if (converged) {
  note = `Converged. ${consolidateNote}`
} else {
  const parts = []
  if (estRemaining > 0) parts.push(`~${estRemaining} files still estimated remaining`)
  if (failures.length) parts.push(`${failures.length} failed`)
  if (unsafeSkipped.length) parts.push(`${unsafeSkipped.length} unsafe paths refused`)
  note = `NOT fully converged (${parts.join('; ') || 'no actionable files this round'}) — rerun for retriable files; unsafe paths need manual handling. ${consolidateNote}`
}
return {
  rule,
  pattern,
  rounds: round,
  converged,
  migratedCount: completed.length,
  migratedFiles: completed,
  skippedFiles: skipped,
  failures,
  unsafeSkipped,
  remainingEstimate: estRemaining,
  note,
}
