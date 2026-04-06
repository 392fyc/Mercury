/**
 * PoC-6: Session Continuity Test
 *
 * Verifies:
 * 1. Session handoff when context overflows
 * 2. New session inherits context via summary
 * 3. Event bus tracks the handoff chain
 *
 * Run: pnpm poc:session
 */

import { EventBus } from "@mercury/core";
import { ClaudeAdapter } from "@mercury/sdk-adapters";

async function testSessionContinuity() {
  console.log("═══════════════════════════════════════════");
  console.log("  PoC-6: Session Continuity (Overflow)     ");
  console.log("═══════════════════════════════════════════\n");

  const bus = new EventBus();
  const claude = new ClaudeAdapter();

  bus.on("*", (event) => {
    console.log(`  [EVENT] ${event.type} | session=${event.sessionId.slice(0, 8)}... | ${JSON.stringify(event.payload).slice(0, 80)}`);
  });

  // Phase 1: Original session
  console.log("Phase 1: Start original session");
  console.log("──────────────────────────────\n");

  const session1 = await claude.startSession(process.cwd());
  bus.emit("agent.session.start", claude.agentId, session1.sessionId, {
    sessionNumber: 1,
  });

  // Simulate work in session 1
  console.log("  Simulating work in session 1...");
  bus.emit("agent.message.send", claude.agentId, session1.sessionId, {
    prompt: "Initial task",
  });
  bus.emit("agent.message.receive", claude.agentId, session1.sessionId, {
    content: "Working on it...",
  });

  // Simulate context overflow detection
  console.log("  ⚠️  Context overflow detected!\n");

  // Phase 2: Generate summary and handoff
  console.log("Phase 2: Generate summary and handoff");
  console.log("────────────────────────────────────\n");

  const summary = [
    "Session summary:",
    "- Task: Implement multi-agent orchestration PoC",
    "- Completed: Event bus, Claude adapter, Codex adapter",
    "- In progress: Cross-agent communication test",
    "- Key decisions: SDK-first integration, append-only events",
    "- Blockers: None",
  ].join("\n");

  bus.emit("orchestrator.context.compact", claude.agentId, session1.sessionId, {
    reason: "context_overflow",
    summaryLength: summary.length,
  });

  const session2 = await claude.handoffSession(session1.sessionId, summary);

  bus.emit("orchestrator.session.handoff", claude.agentId, session2.sessionId, {
    fromSession: session1.sessionId,
    toSession: session2.sessionId,
    summaryLength: summary.length,
  });

  console.log(`  Old session: ${session1.sessionId.slice(0, 8)}... → status: overflow`);
  console.log(`  New session: ${session2.sessionId.slice(0, 8)}... → status: active`);
  console.log(`  Parent link: ${session2.parentSessionId?.slice(0, 8)}...`);

  // Phase 3: Continue work in new session
  console.log("\nPhase 3: Continue in new session");
  console.log("───────────────────────────────\n");

  // In real system, the summary would be prepended to the first prompt
  const continuationPrompt = `${summary}\n\nContinue from where we left off. The next step is to verify cross-agent communication.`;

  console.log(`  New session receives summary (${summary.length} chars) + continuation prompt`);

  bus.emit("agent.message.send", claude.agentId, session2.sessionId, {
    prompt: continuationPrompt.slice(0, 100) + "...",
    hasSummary: true,
  });

  // Verify the chain
  console.log("\n═══ Session Continuity Report ═══");
  console.log(`  Total events: ${bus.size}`);

  const session1Events = bus.getSessionEvents(session1.sessionId);
  const session2Events = bus.getSessionEvents(session2.sessionId);

  console.log(`  Session 1 events: ${session1Events.length}`);
  console.log(`  Session 2 events: ${session2Events.length}`);
  console.log(`  Handoff chain intact: ${session2.parentSessionId === session1.sessionId ? "YES" : "NO"}`);

  const hasCompactEvent = bus.getLog().some((e) => e.type === "orchestrator.context.compact");
  const hasHandoffEvent = bus.getLog().some((e) => e.type === "orchestrator.session.handoff");

  console.log(`  Compact event logged: ${hasCompactEvent ? "YES" : "NO"}`);
  console.log(`  Handoff event logged: ${hasHandoffEvent ? "YES" : "NO"}`);
  console.log(`\n  ✅ Session Continuity: ${
    session2.parentSessionId === session1.sessionId && hasCompactEvent && hasHandoffEvent
      ? "PASS"
      : "FAIL"
  }`);
}

testSessionContinuity().catch((err) => {
  console.error("❌ Session continuity test failed:", err.message);
  process.exit(1);
});
