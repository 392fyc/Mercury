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

// Caps are operator-overridable via args but clamped to a hard ceiling, so the #385
// "explicit upper bound" guardrail holds even when a caller passes an oversized value.
const BATCH_CAP = Math.min((args && args.batchCap) || 12, 50)   // files transformed per round
const MAX_ROUNDS = Math.min((args && args.maxRounds) || 5, 20)  // loop-until-dry safety bound

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
let round = 0
let remaining = 0

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
  // Drop unsafe paths and already-migrated files BEFORE the per-round cap, so the cap
  // budgets real remaining work rather than duplicates or out-of-repo paths. Without the
  // dedup, a Discover that re-returns a completed file could re-migrate it and burn rounds.
  const candidates = rawFiles.filter(x => x && isSafeRelPath(x.file) && !completedSet.has(x.file))
  const droppedBad = rawFiles.length - candidates.length
  if (droppedBad > 0) log(`Round ${round}: dropped ${droppedBad} discovered entries (unsafe path or already-migrated)`)
  const files = candidates.slice(0, BATCH_CAP)
  // If valid candidates exceed BATCH_CAP, the extras are NOT lost: they go un-migrated
  // this round and the loop re-discovers them next round. Log it so the cap is never a
  // silent drop (consistent with the audit template's overflow accounting).
  const overReturned = candidates.length - files.length
  if (overReturned > 0) log(`Round ${round}: Discover over-returned — ${overReturned} files beyond BATCH_CAP=${BATCH_CAP} deferred (counted in remaining; loop re-discovers them)`)
  // Fold over-returned files into `remaining` so the round-cap boundary cannot exit with
  // "Converged / remainingEstimate: 0" while sliced-off files are still un-migrated. The
  // loop re-discovers them on the next round; at the round cap they surface in the return.
  remaining = ((found && found.remainingEstimate) || 0) + overReturned
  if (!files.length) { log(`Round ${round}: no files left — migration converged`); break }
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

const hitRoundCap = round >= MAX_ROUNDS && remaining > 0
if (hitRoundCap) log(`Stopped at MAX_ROUNDS=${MAX_ROUNDS} with ~${remaining} files still estimated remaining (not dropped — rerun to continue)`)

const consolidateNote = 'Edits are in the working tree, uncommitted — review the diff and commit (Main manages git).'
return {
  rule,
  pattern,
  rounds: round,
  migratedCount: completed.length,
  migratedFiles: completed,
  skippedFiles: skipped,
  failures,
  remainingEstimate: hitRoundCap ? remaining : 0,
  note: hitRoundCap
    ? `Round cap reached — rerun the workflow to migrate the rest. ${consolidateNote}`
    : `Converged. ${consolidateNote}`,
}
