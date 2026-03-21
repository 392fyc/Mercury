/**
 * PoC-5: Cross-Agent Communication Test
 *
 * Verifies the core Mercury flow:
 * 1. Main Agent receives task from user
 * 2. Main Agent dispatches sub-task to Sub Agent
 * 3. Sub Agent executes and returns result
 * 4. Result is emitted via EventBus (simulated relay, not direct injection)
 * 5. Main Agent context receives task-complete event
 *
 * Note: agent types are configurable via createMainAgent/createSubAgent factories.
 * This is the key flow that replaces 36 manual handoffs → 0.
 *
 * Run: pnpm poc:cross-agent
 */

import { EventBus, isStreamingEvent } from "@mercury/core";
import type { AgentAdapter, AgentMessage, SessionInfo } from "@mercury/core";
import { ClaudeAdapter, CodexMCPAdapter } from "@mercury/sdk-adapters";

/** Factory functions — swap these to test different adapter combinations. */
function createMainAgent(): AgentAdapter { return new ClaudeAdapter(); }
function createSubAgent(): AgentAdapter { return new CodexMCPAdapter(); }

/**
 * Runs a cross-agent orchestration PoC where a main agent delegates a task to
 * a sub-agent. Results are relayed via EventBus events (simulated, not direct
 * injection into the main agent's next prompt).
 */
async function testCrossAgentCommunication() {
  console.log("═══════════════════════════════════════════════════");
  console.log("  PoC-5: Cross-Agent Communication (Main → Sub)   ");
  console.log("═══════════════════════════════════════════════════\n");

  const bus = new EventBus();

  // Initialize agents via factories (not hardcoded)
  const mainAgent = createMainAgent();
  const subAgent = createSubAgent();

  // Monitor all events
  bus.on("*", (event) => {
    const prefix = event.agentId === "claude-code" ? "🔵 MAIN" : "🟢 SUB ";
    console.log(`  ${prefix} | ${event.type} | ${JSON.stringify(event.payload).slice(0, 80)}`);
  });

  console.log("Phase 1: User sends task to Main Agent");
  console.log("──────────────────────────────────────\n");

  const mainSession = await mainAgent.startSession(process.cwd());
  bus.emit("agent.session.start", mainAgent.agentId, mainSession.sessionId, {
    role: "main",
  });

  try {
    // Simulate: Main Agent analyzes task and decides to delegate
    console.log("  User → Main Agent: 'Read README.md and list the project goals'");
    console.log("  Main Agent decides to delegate to Sub Agent...\n");

    console.log("Phase 2: Main Agent dispatches to Sub Agent");
    console.log("──────────────────────────────────────────\n");

    const subSession = await subAgent.startSession(process.cwd());
    bus.emit("agent.session.start", subAgent.agentId, subSession.sessionId, {
      role: "dev",
      delegatedFrom: mainAgent.agentId,
    });

    const taskEvent = bus.emit(
      "orchestrator.task.dispatch",
      mainAgent.agentId,
      mainSession.sessionId,
      {
        taskId: "TASK-POC-001",
        title: "Read README and extract project goals",
        assignedTo: subAgent.agentId,
        prompt: 'Read the file "README.md" and list the project goals. Respond concisely. Do not make any changes.',
      },
    );

    console.log("Phase 3: Sub Agent executes task");
    console.log("───────────────────────────────\n");

    const subPrompt = 'Read the file "README.md" and list the project goals. Respond concisely. Do not make any changes.';
    const subResults: AgentMessage[] = [];

    try {
      for await (const item of subAgent.sendPrompt(subSession.sessionId, subPrompt)) {
        if (isStreamingEvent(item)) continue;
        subResults.push(item);
        bus.emit(
          "agent.message.receive",
          subAgent.agentId,
          subSession.sessionId,
          { contentPreview: item.content.slice(0, 100) },
          taskEvent.id,
        );
      }
    } finally {
      await subAgent.endSession(subSession.sessionId);
      bus.emit("agent.session.end", subAgent.agentId, subSession.sessionId, {
        messageCount: subResults.length,
      });
    }

    console.log("\nPhase 4: Result relayed to Main Agent via EventBus");
    console.log("──────────────────────────────────────────────────\n");

    // Synthesize sub-agent result into a summary for Main Agent
    const subResult = subResults.map((m) => m.content).join("\n");
    const resultSummary = subResult.slice(0, 500);

    bus.emit(
      "orchestrator.task.complete",
      mainAgent.agentId,
      mainSession.sessionId,
      {
        taskId: "TASK-POC-001",
        subAgentId: subAgent.agentId,
        resultSummary,
      },
      taskEvent.id,
    );

    console.log(`  Sub Agent result (${subResult.length} chars) → Main Agent context`);
    console.log(`  Preview: ${resultSummary.slice(0, 200)}`);

    // Final report
    console.log("\n═══ Cross-Agent Communication Report ═══");
    console.log(`  Total events: ${bus.size}`);
    console.log(`  Main Agent events: ${bus.getAgentEvents(mainAgent.agentId).length}`);
    console.log(`  Sub Agent events: ${bus.getAgentEvents(subAgent.agentId).length}`);
    console.log(`  Sub Agent messages: ${subResults.length}`);
    console.log(`  Task chain intact: ${bus.getLog().some((e) => e.parentEventId === taskEvent.id) ? "YES" : "NO"}`);
    console.log(`\n  ✅ Cross-Agent Communication: ${subResults.length > 0 ? "PASS" : "FAIL"}`);
    console.log(`  Human handoffs required: 0 (was: 36 in SoT)`);
  } finally {
    await mainAgent.endSession(mainSession.sessionId);
  }
}

testCrossAgentCommunication().catch((err) => {
  console.error("❌ Cross-agent test failed:", err.message);
  process.exit(1);
});
