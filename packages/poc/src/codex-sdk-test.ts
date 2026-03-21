/**
 * PoC-2: Codex CLI SDK Integration Test
 *
 * Verifies:
 * 1. SDK import and Codex class instantiation
 * 2. Thread creation and prompt execution
 * 3. Result return
 *
 * Run: pnpm poc:codex
 */

import { EventBus, isStreamingEvent } from "@mercury/core";
import { CodexMCPAdapter } from "@mercury/sdk-adapters";

/**
 * Executes an end-to-end integration test of the Codex CLI SDK: starts a session, sends a README.md summary prompt, consumes streamed responses, and ends the session.
 *
 * Emits agent lifecycle and message events on an internal EventBus and logs events, received messages, and a pass/fail result based on whether any messages were received.
 */
async function testCodexSDK() {
  console.log("═══════════════════════════════════════");
  console.log("  PoC-2: Codex CLI SDK Integration     ");
  console.log("═══════════════════════════════════════\n");

  const bus = new EventBus();
  const codex = new CodexMCPAdapter();

  bus.on("*", (event) => {
    console.log(`[EVENT] ${event.type} | agent=${event.agentId} | ${JSON.stringify(event.payload).slice(0, 100)}`);
  });

  // Step 1: Start session
  console.log("Step 1: Starting Codex CLI session...");
  const session = await codex.startSession(process.cwd());
  bus.emit("agent.session.start", codex.agentId, session.sessionId, {
    cwd: process.cwd(),
  });
  console.log(`  Session ID: ${session.sessionId}`);

  // Step 2: Send prompt
  console.log("\nStep 2: Sending prompt...");
  const prompt = 'Read the file "README.md" and respond with a one-sentence summary. Do not make any changes.';

  bus.emit("agent.message.send", codex.agentId, session.sessionId, { prompt });

  let messageCount = 0;
  let lastContent = "";

  for await (const item of codex.sendPrompt(session.sessionId, prompt)) {
    if (isStreamingEvent(item)) continue;
    messageCount++;
    lastContent = item.content;
    bus.emit("agent.message.receive", codex.agentId, session.sessionId, {
      role: item.role,
      contentPreview: item.content.slice(0, 200),
    });
    console.log(`  [${item.role}] ${item.content.slice(0, 200)}`);
  }

  // End session
  await codex.endSession(session.sessionId);
  bus.emit("agent.session.end", codex.agentId, session.sessionId, { messageCount });

  console.log("\n─── Results ───");
  console.log(`  Messages received: ${messageCount}`);
  console.log(`  Event bus events: ${bus.size}`);
  console.log(`  ✅ Codex SDK integration: ${messageCount > 0 ? "PASS" : "FAIL"}`);
}

testCodexSDK().catch((err) => {
  console.error("❌ Codex SDK test failed:", err.message);
  process.exit(1);
});
