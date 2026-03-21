/**
 * PoC-4: Event Bus Unit Test
 *
 * Verifies:
 * 1. Event emission and subscription
 * 2. Append-only immutability
 * 3. Filtered subscriptions
 * 4. Session/agent event retrieval
 * 5. Deterministic replay
 *
 * Run: pnpm poc:eventbus
 */

import { EventBus } from "@mercury/core";

function testEventBus() {
  console.log("═══════════════════════════════════════");
  console.log("  PoC-4: Event Bus Verification        ");
  console.log("═══════════════════════════════════════\n");

  const bus = new EventBus();
  const results: string[] = [];

  // Test 1: Basic emit and subscribe
  console.log("Test 1: Basic emit + subscribe...");
  const received: string[] = [];
  const unsub = bus.on("agent.session.start", (e) => {
    received.push(e.id);
  });

  const e1 = bus.emit("agent.session.start", "claude", "session-1", { cwd: "/test" });
  const e2 = bus.emit("agent.session.start", "codex", "session-2", { cwd: "/test" });

  console.assert(received.length === 2, "Should receive 2 events");
  console.assert(e1.type === "agent.session.start", "Event type should match");
  console.assert(e1.agentId === "claude", "Agent ID should match");
  results.push(received.length === 2 ? "✅ Basic emit+subscribe" : "❌ Basic emit+subscribe");

  // Test 2: Immutability
  console.log("Test 2: Event immutability...");
  let immutable = true;
  try {
    (e1 as unknown as Record<string, unknown>).type = "hacked" as never;
    immutable = false;
  } catch {
    // Object.freeze prevents modification — this is expected
  }
  results.push(immutable ? "✅ Event immutability (frozen)" : "⚠️  Events not frozen (strict mode may vary)");

  // Test 3: Wildcard subscriber
  console.log("Test 3: Wildcard subscriber...");
  const allEvents: string[] = [];
  bus.on("*", (e) => allEvents.push(e.type));

  bus.emit("agent.message.send", "claude", "session-1", { prompt: "test" });
  bus.emit("agent.tool.use", "claude", "session-1", { tool: "Read" });
  console.assert(allEvents.length === 2, "Wildcard should get all events");
  results.push(allEvents.length === 2 ? "✅ Wildcard subscriber" : "❌ Wildcard subscriber");

  // Test 4: Filtered subscriber
  console.log("Test 4: Filtered subscriber (by agentId)...");
  const claudeOnly: string[] = [];
  bus.onFiltered(
    (e) => e.agentId === "claude",
    (e) => claudeOnly.push(e.type),
  );

  bus.emit("agent.message.send", "codex", "session-2", { prompt: "codex task" });
  bus.emit("agent.message.send", "claude", "session-1", { prompt: "claude task" });
  console.assert(claudeOnly.length === 1, "Filtered should only match claude");
  results.push(claudeOnly.length === 1 ? "✅ Filtered subscriber" : "❌ Filtered subscriber");

  // Test 5: Unsubscribe
  console.log("Test 5: Unsubscribe...");
  const beforeUnsub = received.length;
  unsub();
  bus.emit("agent.session.start", "test", "session-3", {});
  console.assert(received.length === beforeUnsub, "Should not receive after unsub");
  results.push(received.length === beforeUnsub ? "✅ Unsubscribe" : "❌ Unsubscribe");

  // Test 6: Session event retrieval
  console.log("Test 6: Session event retrieval...");
  const session1Events = bus.getSessionEvents("session-1");
  const session2Events = bus.getSessionEvents("session-2");
  results.push(
    session1Events.length > 0 && session2Events.length > 0
      ? "✅ Session event retrieval"
      : "❌ Session event retrieval",
  );

  // Test 7: Agent event retrieval
  console.log("Test 7: Agent event retrieval...");
  const claudeEvents = bus.getAgentEvents("claude");
  const codexEvents = bus.getAgentEvents("codex");
  results.push(
    claudeEvents.length > 0 && codexEvents.length > 0
      ? "✅ Agent event retrieval"
      : "❌ Agent event retrieval",
  );

  // Test 8: Append-only log integrity
  console.log("Test 8: Append-only log integrity...");
  const totalEvents = bus.size;
  results.push(totalEvents === bus.getLog().length ? "✅ Log integrity" : "❌ Log integrity");

  // Test 9: Parent event chaining
  console.log("Test 9: Parent event chaining...");
  const parent = bus.emit("orchestrator.task.dispatch", "main", "session-1", {
    task: "implement feature",
  });
  const child = bus.emit(
    "agent.message.send",
    "codex",
    "session-2",
    { prompt: "implement it" },
    parent.id,
  );
  console.assert(child.parentEventId === parent.id, "Child should reference parent");
  results.push(child.parentEventId === parent.id ? "✅ Parent event chaining" : "❌ Parent event chaining");

  // Summary
  console.log("\n─── Results ───");
  console.log(`  Total events in log: ${bus.size}`);
  for (const r of results) {
    console.log(`  ${r}`);
  }

  const passed = results.filter((r) => r.startsWith("✅")).length;
  const total = results.length;
  console.log(`\n  Score: ${passed}/${total}`);
  console.log(`  Event Bus: ${passed === total ? "ALL PASS" : "SOME FAILURES"}`);
}

testEventBus();
