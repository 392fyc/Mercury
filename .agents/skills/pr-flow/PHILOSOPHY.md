# Why pr-flow Exists

> Methodology document behind Mercury's `pr-flow` skill. Targets external readers who want to understand the design decisions, not just the operational steps.

## The problem

PR review is where AI agent productivity goes to die.

A coding agent finishes work, opens a PR, and then a human (or a review bot) leaves comments. The agent reads the comments, decides what to fix, fixes some of them, replies to others, pushes a new commit. The reviewer comes back, sees the fixes, possibly resolves threads, possibly adds new findings. This cycle repeats until merge. In a reasonable engineering culture, it might take five rounds for a non-trivial PR.

Every round in that cycle has at least three failure modes for an unaided agent:

1. **The agent claims work is done when it is not.** It pushes a commit that addresses *most* of a thread's concern and posts "Fixed in 1a2b3c", quietly leaving the load-bearing part untouched. Reviewers either trust it (bad) or have to manually re-verify (defeats the purpose).

2. **The agent argues the wrong battles.** A reviewer flags something the agent disagrees with. The agent should explain its reasoning calmly. Instead, in the worst case, it apologizes, makes a meaningless change to satisfy the reviewer, and creates noise. In the second-worst case, it argues stubbornly past the point where the reviewer has made a legitimate call.

3. **The agent loses the plot across rounds.** By round three, the agent forgets which threads are resolved, which are pending fix, and which are pending discussion. It re-fixes things that were already fixed, ignores things that were already raised, and the PR drifts into entropy.

A good pr-flow skill solves all three by making the loop *mechanical* — by handing each decision a deterministic gate and refusing to advance until the gate passes.

## Why the loop is sequential, not async

Mercury's pr-flow runs each phase as a hard gate: create PR, wait for review, fix findings, push, wait again, possibly merge. There is no parallelism. There is no event-driven dispatch. The agent runs one phase, blocks on the gate, then runs the next.

This is unfashionable. Most automation literature would suggest the agent should subscribe to webhooks, react to review events, and process them asynchronously. We tried that. It produces the worst of both worlds: agent behavior that is hard to reason about *and* still fundamentally bottlenecked on human review latency.

Sequential is better because:
- The agent's mental model at any point is just "what phase am I in, what gate am I waiting on" — no concurrent state to track
- Failure modes are localized to a phase
- A human can stop the loop at any phase, intervene, and resume — there is no in-flight async work to clean up
- The skill file becomes readable as a linear protocol, not a state machine

The cost is real: the agent literally sits idle waiting for review. We accept that cost because the alternative is debugging concurrent agent reasoning, which is a much higher-variance failure mode.

## The fix-detection contract with the review bot

Mercury's pr-flow is built around a specific assumption about how the review bot (Argus) behaves:

- **Fix-detection resolve**: when the agent pushes a commit that touches the file and lines flagged by an open thread, the bot auto-resolves that thread. The agent does NOT manually mark threads resolved.
- **No "Fixed in 1a2b3c" comments**: the diff is the explanation. Posting fix announcements clutters the thread without adding signal.
- **APPROVE only on zero new findings**: the bot returns COMMENT (not APPROVE) until a review iteration produces no new findings AND all prior threads are resolved.
- **Reply-aware resolution**: when the agent disagrees with a finding, it replies with reasoning. The bot's LLM classifies the reply as ACCEPT, REJECT, or ESCALATE, and resolves accordingly. Maximum three reply rounds before a thread escalates to a human.

These rules are not abstract. They are baked into the protocol because they short-circuit the three failure modes above.

- The agent cannot claim work is done — only the bot's fix detection can resolve a thread.
- The agent cannot argue wrong battles indefinitely — the reply-round cap forces escalation.
- The agent cannot lose the plot — thread state lives in the bot, not in the agent's head.

## Why the agent never resolves threads itself

This is the most counterintuitive rule and the one engineers push back on the hardest. Surely the agent should be able to mark its own work resolved when it has clearly fixed something?

No. Because "clearly fixed" is exactly the failure mode. Self-resolution is the soliloquy problem applied to PR review. The point of having a separate review bot is having an independent judge of whether a thread is closed. If the agent overrides that judge, the bot becomes ceremonial.

The behavior is uncomfortable in the short term — the agent commits a fix and the thread sits open until the bot's next pass — and it is correct in the long term, because it forces the agent to write fixes that are *visible* in the diff at the precise location the bot can detect them. That is also a nudge toward better fixes: targeted edits at the flagged lines, not sprawling refactors that incidentally happen to touch the area.

## Why a maximum of five iterations

The loop cap mirrors the dev-pipeline cap (three iterations) but is set higher because PR review is structurally noisier — review bots sometimes flag the same class of thing across multiple files, and some legitimate PRs need three or four passes just because the diff is large. Five is the empirical sweet spot where:
- legitimate large PRs converge in time
- pathological cases (the agent stuck in a fix-find-fix loop on the same finding) hit the cap and escalate
- the cap is low enough that escalation is cheap

Above five iterations, every additional round dramatically increases the chance the agent is "fixing" the wrong thing.

## What this skill is NOT

- **Not a code review tool.** It does not generate review comments. That is the bot's job.
- **Not a CI integration layer.** It assumes CI runs separately. If CI fails, the human resolves it; the skill does not interpret CI output.
- **Not a merge policy engine.** It does not enforce branch protection rules. GitHub does that. The skill just respects them.
- **Not a generic GitHub automation.** It is specifically the Mercury PR loop with the Argus bot's fix-detection contract. Replace Argus with a bot that does not auto-resolve, and the skill needs adaptation.

## What it IS

A protocol that says: open a PR, wait for review, fix or reply per finding, push, wait for the bot's next pass, repeat until APPROVE, then merge. With one critical behavioral constraint: never claim work yourself, never resolve threads yourself, never argue past three reply rounds. The protocol is short. The constraints are the methodology.

## Closing

PR review is one of the highest-leverage moments in any software engineering workflow. It is also the moment where AI agents most commonly underperform, because every PR cycle is a small calibration test and agents are bad at calibrating themselves. pr-flow does not try to make the agent better at calibration. It removes the agent's ability to self-judge and delegates the judgment to the review bot, which is the only independent voice in the loop. That delegation is the entire trick.
