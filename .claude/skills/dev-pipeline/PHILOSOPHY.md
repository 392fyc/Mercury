# Why a Preset Dev Pipeline?

> This document is the methodology narrative behind Mercury's `dev-pipeline` skill. It targets external readers — open-source users, video viewers, blog readers — who want to understand the reasoning, not just the recipe.

## The problem

When AI agents do real engineering work, two failure modes dominate.

**Failure 1: the soliloquy.** A single agent implements, tests, and judges its own work. Because it remembers writing the code, it remembers being convinced the code was right, and it grades itself accordingly. Modern models are well-calibrated at the token level and badly calibrated at the "is this whole thing actually done" level. They reach the end of an implementation and report success because that is the conversational gravity of their own narrative arc, not because the code passes.

**Failure 2: the orchestrator tax.** The natural reaction to Failure 1 is to build a multi-agent orchestration framework: a coordinator agent that creates plans, dispatches workers, polls statuses, retries on failure, escalates on stuck. This solves the soliloquy problem and immediately introduces a worse one. The orchestrator becomes the product. Every iteration of the underlying model is now bottlenecked by the orchestrator's assumptions about what the model can or cannot do. When the model improves, the orchestrator does not get better — it gets in the way.

Mercury was originally Failure 2. Phase 0 archived that orchestrator. Phase 1 had to answer: if not orchestration, then what?

## The answer: preset chains

A **preset chain** is a fixed, hand-designed sequence of agent invocations that solves one specific shape of problem. It is not dynamic. It does not decide what to do. It does not branch on intent. The only thing it knows how to do is run a single coding task end-to-end with two roles in the loop: **Dev** writes, **Acceptance** judges blind.

That is the entire dev pipeline. Two roles. One direction. No branching except a bounded retry loop.

Why this works:

1. **It solves the soliloquy without inventing orchestration.** Dev does not grade itself. Acceptance does not write code. The hand-off is one-way and the receiver is structurally prevented out of seeing the senders narrative. Calibration improves because the judge has no stake in the conviction of the implementer.

2. **It gets out of the model's way.** The chain has no opinion about *how* to implement. It only specifies *what* to verify. As the model improves, the implementation gets better. The chain does not need to be rewritten.

3. **It is small enough to read in five minutes.** A preset chain is a `.md` file. No DAG engine, no event bus, no state machine framework. The whole specification fits on one screen, which is the right size for a methodology you want other developers to copy.

## Why blind acceptance

The blindness is the load-bearing constraint. If acceptance reads the dev agent's notes, evidence pointers, or risk callouts, it inherits the dev agent's framing, and the verification collapses back into Failure 1 with extra steps.

Blindness is enforced two ways:

- **Structurally**: the prompt template strips narrative fields out of the receipt before passing it to acceptance. The acceptance agent literally cannot read what dev concluded.
- **Behaviorally**: the acceptance agent's role definition explicitly forbids inferring or asking about dev's reasoning.

This is uncomfortable. It feels wasteful — surely letting acceptance see what dev was thinking would be more efficient? It is not. Efficiency is the wrong axis. The whole point of a second agent is *independent judgment*, and independence dies the moment the second agent is told what the first agent believed.

## Why a maximum of three iterations

The retry loop exists because acceptance sometimes catches a real bug and dev should fix it. But infinite retries are how agents quietly burn budget on convergence failures. The cap is mechanical — three iterations and the chain escalates to the human, no judgment call.

Three is not magic. Two is too few (one mistake, one fix, no slack). Four is too many (by then the failure mode is usually that acceptance keeps surfacing the same finding because dev is fixing the wrong thing — and the fourth pass will do the same). Three forces the human in the loop early enough that the divergence is still cheap to repair.

## What this skill is NOT

- **Not an orchestrator.** It does not pick tasks, decide priorities, or coordinate parallel work. The user does that. If the user wants parallel tasks, the user opens another session.
- **Not a workflow engine.** It does not retry on infrastructure flake, manage queues, or persist state between runs. Each invocation is a fresh chain.
- **Not a quality gate replacement.** Phase 2 will mount real quality gates (lint, type-check, test enforcement) via external project submodules. The dev pipeline assumes those gates exist in the repo it runs against; it does not implement them.

## What it IS

A 220-line markdown file that says: when a coding task is well-scoped and you want a second opinion, run it through this loop. Copy the file into any GitHub-based repository that has `.claude/agents/dev.md` and `.claude/agents/acceptance.md` defined, strip the Mercury-specific `/gh-project-flow` line out of Phase 6, and you are done.

The methodology is the asset. The skill file is the artifact.

## Extending it

Three obvious extensions, none of which belong in the baseline:

- **Add a critic pass on a different model.** Useful when the dev model and acceptance model are the same — a third independent perspective on a different model substrate breaks self-congratulation bias completely. Out of scope until Phase 2.
- **Persist learnings between runs.** When dev keeps making the same mistake across tasks, that mistake should become a constraint in the next TaskBundle. This is the Memory Layer story (Phase 3).
- **Parallel task fan-out.** Mercury explicitly does not do this. If you want it, open multiple sessions. Parallel coordination across sessions is the agent-teams story, not the dev-pipeline story.

## Closing

The temptation, when you watch agents fail at coding, is to build more machinery around them. The lesson Mercury keeps relearning is that more machinery is what made them fail in the first place. The dev pipeline is small on purpose. The two agents in it are dumb on purpose. The chain has no policy on purpose. What it has, and what nothing else gives you, is **independent judgment under structural blindness** — and that turns out to be the only thing the soliloquy problem actually responds to.
