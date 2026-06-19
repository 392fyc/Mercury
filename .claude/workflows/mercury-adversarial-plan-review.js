export const meta = {
  name: 'mercury-adversarial-plan-review',
  description: 'Draft a plan from several independent angles, score them with an adversarial judge panel, then synthesize from the winner while grafting the best ideas from runners-up',
  whenToUse: 'A hard, wide-solution-space decision worth more than one pass: architecture choice, migration strategy, risky refactor plan. Beats one-attempt-iterated because the judge panel surfaces failure modes a single drafter misses. Hands the synthesized plan back to Main for dispatch — does NOT implement.',
  phases: [
    { title: 'Draft', detail: 'independent plans, each from a different angle' },
    { title: 'Judge', detail: 'adversarial panel scores every draft' },
    { title: 'Synthesize', detail: 'winner + grafted best ideas + residual risks' },
  ],
}

// ── Mercury guardrails (#385) ──
// 1. LEAN DISPATCH: drafters/judges are given the task statement + paths to read, not
//    the full contents of context files. Each agent reads the cited files itself.
// 2. ANGLE_CAP bounds how many independent drafts are produced.
// 3. Read-only: this workflow produces a plan + risk analysis. Implementation stays with
//    Main -> dev (CLAUDE.md: never self-approve; authoring and review are separate passes).

const task = (args && (args.task || args.question)) || (typeof args === 'string' ? args : null)
if (!task) {
  log('No task/plan provided. Pass one via args, e.g. /mercury-adversarial-plan-review "how should we shard the event store?"')
  return { error: 'missing task' }
}
// Optional: paths the drafters should read for context (lean — paths only, not contents).
const contextPaths = (args && args.contextPaths) || []
const ctxLine = contextPaths.length ? `\nRead these for context (do not assume their contents): ${contextPaths.join(', ')}` : ''

const ANGLE_CAP = (args && args.maxAngles) || 4
const ANGLES = [
  { key: 'mvp-first', lens: 'ship the smallest correct thing first; defer everything deferrable' },
  { key: 'risk-first', lens: 'minimize blast radius and irreversibility; prioritize rollback and guardrails' },
  { key: 'user-first', lens: 'optimize the end-user / operator experience and observable behavior' },
  { key: 'maintenance-first', lens: 'optimize long-term maintainability, modularity, and detachability' },
].slice(0, ANGLE_CAP)

const PLAN_SCHEMA = {
  type: 'object',
  required: ['summary', 'steps', 'risks'],
  properties: {
    summary: { type: 'string' },
    steps: { type: 'array', items: { type: 'string' } },
    risks: { type: 'array', items: { type: 'string' } },
    tradeoffs: { type: 'string' },
  },
}

const SCORE_SCHEMA = {
  type: 'object',
  required: ['scores', 'overall', 'killer'],
  properties: {
    scores: {
      type: 'object',
      properties: {
        correctness: { type: 'number' },
        risk: { type: 'number' },
        feasibility: { type: 'number' },
        maintainability: { type: 'number' },
      },
    },
    overall: { type: 'number', description: '0-10 weighted overall' },
    killer: { type: 'string', description: 'the single strongest reason this plan could fail' },
    bestIdea: { type: 'string', description: 'the one idea from this plan worth keeping even if it loses' },
  },
}

phase('Draft')
const drafts = await parallel(ANGLES.map(a => () =>
  agent(
    `Draft a concrete plan for this task through the **${a.key}** lens (${a.lens}):\n"${task}"${ctxLine}\n` +
    `Return a summary, ordered steps, explicit risks, and the key trade-off. Be specific and opinionated for this lens.`,
    { label: `draft:${a.key}`, phase: 'Draft', schema: PLAN_SCHEMA, agentType: 'design' }
  ).then(p => ({ angle: a.key, plan: p }))
))
const validDrafts = drafts.filter(d => d && d.plan)
log(`${validDrafts.length} drafts produced`)

phase('Judge')
// Each draft is scored by an independent adversarial judge (different lane than the
// drafter — no self-congratulation). Judges are told to hunt for the killer failure mode.
const judged = await parallel(validDrafts.map(d => () =>
  agent(
    `Adversarially evaluate this plan for the task: "${task}".\n` +
    `Plan (${d.angle}): ${JSON.stringify(d.plan)}\n` +
    `Score correctness/risk/feasibility/maintainability (0-10 each) and an overall. ` +
    `Identify the single strongest reason it could FAIL (killer), and the one idea worth keeping even if it loses (bestIdea). Be skeptical.`,
    { label: `judge:${d.angle}`, phase: 'Judge', schema: SCORE_SCHEMA, agentType: 'critic' }
  ).then(s => ({ ...d, score: s }))
))

phase('Synthesize')
const scored = judged.filter(j => j && j.score).sort((a, b) => (b.score.overall || 0) - (a.score.overall || 0))
if (!scored.length) return { task, error: 'no scored drafts' }
const winner = scored[0]
const runnersUp = scored.slice(1)

const synthesis = await agent(
  `Synthesize the final recommended plan for: "${task}".\n` +
  `Winning approach (${winner.angle}, overall ${winner.score.overall}): ${JSON.stringify(winner.plan)}\n` +
  `Killer risk to neutralize: ${winner.score.killer}\n` +
  `Best ideas to graft from runners-up: ${runnersUp.map(r => `[${r.angle}] ${r.score.bestIdea}`).join(' | ') || '(none)'}\n` +
  `Produce one coherent plan that takes the winner as the spine, grafts the best runner-up ideas, and explicitly addresses the killer risk. ` +
  `End with residual risks that survive synthesis.`,
  { label: 'synthesize', phase: 'Synthesize', schema: PLAN_SCHEMA, agentType: 'design' }
)

return {
  task,
  winningAngle: winner.angle,
  scoreboard: scored.map(s => ({ angle: s.angle, overall: s.score.overall, killer: s.score.killer })),
  recommendedPlan: synthesis,
  note: 'Plan only — dispatch implementation to dev via Main; do not self-implement.',
}
