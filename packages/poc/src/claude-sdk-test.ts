/**
 * PoC-1: Claude Agent SDK Integration Test
 *
 * Verifies:
 * 1. SDK import and query() function works
 * 2. Can start a session and send a prompt
 * 3. Streaming response is received
 * 4. Session ID is captured for resume
 *
 * Run: pnpm poc:claude
 */

import { EventBus } from "@mercury/core";
import { ClaudeAdapter } from "@mercury/sdk-adapters";

async function testClaudeSDK() {
  console.log("═══════════════════════════════════════");
  console.log("  PoC-1: Claude Agent SDK Integration  ");
  console.log("═══════════════════════════════════════\n");

  const bus = new EventBus();
  const claude = new ClaudeAdapter();

  // Monitor events
  bus.on("*", (event) => {
    console.log(`[EVENT] ${event.type} | agent=${event.agentId} | ${JSON.stringify(event.payload).slice(0, 100)}`);
  });

  // Step 1: Start session
  console.log("Step 1: Starting Claude Code session...");
  const session = await claude.startSession(process.cwd());
  bus.emit("agent.session.start", claude.agentId, session.sessionId, {
    cwd: process.cwd(),
  });
  console.log(`  Session ID: ${session.sessionId}`);
  console.log(`  Status: ${session.status}\n`);

  // Step 2: Send a simple prompt
  console.log("Step 2: Sending prompt...");
  const prompt = 'Read the file "README.md" and respond with a one-sentence summary of the project. Do not make any changes.';

  bus.emit("agent.message.send", claude.agentId, session.sessionId, {
    prompt,
  });

  let messageCount = 0;
  let lastContent = "";

  for await (const message of claude.sendPrompt(session.sessionId, prompt)) {
    messageCount++;
    lastContent = message.content;
    bus.emit("agent.message.receive", claude.agentId, session.sessionId, {
      role: message.role,
      contentPreview: message.content.slice(0, 200),
      metadata: message.metadata,
    });
    console.log(`  [${message.role}] ${message.content.slice(0, 200)}`);
  }

  // Step 3: End session
  await claude.endSession(session.sessionId);
  bus.emit("agent.session.end", claude.agentId, session.sessionId, {
    messageCount,
  });

  // Report
  console.log("\n─── Results ───");
  console.log(`  Messages received: ${messageCount}`);
  console.log(`  Event bus events: ${bus.size}`);
  console.log(`  Last content: ${lastContent.slice(0, 200)}`);
  console.log(`  ✅ Claude SDK integration: ${messageCount > 0 ? "PASS" : "FAIL"}`);
}

testClaudeSDK().catch((err) => {
  console.error("❌ Claude SDK test failed:", err.message);
  process.exit(1);
});
