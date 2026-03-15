/**
 * PoC-3: opencode Integration Test
 *
 * Verifies:
 * 1. opencode run --format json one-shot execution
 * 2. Result parsing
 *
 * Run: pnpm poc:opencode
 */

import { EventBus } from "@mercury/core";
import { OpencodeAdapter } from "@mercury/sdk-adapters";

async function testOpencode() {
  console.log("═══════════════════════════════════════");
  console.log("  PoC-3: opencode Integration          ");
  console.log("═══════════════════════════════════════\n");

  const bus = new EventBus();
  const oc = new OpencodeAdapter();

  bus.on("*", (event) => {
    console.log(`[EVENT] ${event.type} | agent=${event.agentId} | ${JSON.stringify(event.payload).slice(0, 100)}`);
  });

  console.log("Step 1: Starting opencode session...");
  const session = await oc.startSession(process.cwd());
  bus.emit("agent.session.start", oc.agentId, session.sessionId, {
    cwd: process.cwd(),
  });
  console.log(`  Session ID: ${session.sessionId}`);

  console.log("\nStep 2: Sending prompt (one-shot)...");
  const prompt = 'Read the file "README.md" and respond with a one-sentence summary. Do not make any changes.';

  bus.emit("agent.message.send", oc.agentId, session.sessionId, { prompt });

  let messageCount = 0;
  for await (const message of oc.sendPrompt(session.sessionId, prompt)) {
    messageCount++;
    bus.emit("agent.message.receive", oc.agentId, session.sessionId, {
      role: message.role,
      contentPreview: message.content.slice(0, 200),
    });
    console.log(`  [${message.role}] ${message.content.slice(0, 200)}`);
  }

  await oc.endSession(session.sessionId);
  await oc.shutdown();

  console.log("\n─── Results ───");
  console.log(`  Messages received: ${messageCount}`);
  console.log(`  ✅ opencode integration: ${messageCount > 0 ? "PASS" : "FAIL"}`);
}

testOpencode().catch((err) => {
  console.error("❌ opencode test failed:", err.message);
  process.exit(1);
});
