/**
 * Mercury Orchestrator — Entry Point
 *
 * Spawned by Tauri as a sidecar process.
 * Communicates via JSON-RPC 2.0 over stdin/stdout.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { AgentConfig } from "@mercury/core";
import { RpcTransport } from "./rpc-transport.js";
import { AgentRegistry } from "./agent-registry.js";
import { Orchestrator } from "./orchestrator.js";

interface MercuryConfig {
  agents: AgentConfig[];
  workDir?: string;
}

function loadConfig(configPath?: string): MercuryConfig {
  const paths = [
    configPath,
    resolve(process.cwd(), "mercury.config.json"),
    resolve(process.env.HOME ?? process.env.USERPROFILE ?? "", ".mercury", "config.json"),
  ].filter(Boolean) as string[];

  for (const p of paths) {
    try {
      const raw = readFileSync(p, "utf-8");
      const config = JSON.parse(raw) as MercuryConfig;
      transport.log(`Loaded config from ${p} (${config.agents.length} agents)`);
      return config;
    } catch {
      // try next
    }
  }

  transport.log("No config found, using defaults");
  return {
    agents: [
      {
        id: "claude-code",
        displayName: "Claude Code",
        cli: "claude",
        role: "main",
        integration: "sdk",
        capabilities: ["code", "review", "orchestration"],
        restrictions: [],
        maxConcurrentSessions: 3,
      },
      {
        id: "codex-cli",
        displayName: "Codex CLI",
        cli: "codex",
        role: "dev",
        integration: "sdk",
        capabilities: ["code", "test"],
        restrictions: [],
        maxConcurrentSessions: 2,
      },
    ],
  };
}

// Bootstrap
const transport = new RpcTransport();
const configPath = process.argv[2];
const config = loadConfig(configPath);

if (config.workDir) {
  process.chdir(resolve(process.cwd(), config.workDir));
}

const registry = new AgentRegistry(config.agents);
const orchestrator = new Orchestrator(registry, transport);

transport.start((method, params) => orchestrator.handleRpc(method, params));

// Signal ready
transport.sendNotification("ready", {
  agents: registry.listAgents().map((a) => a.id),
  timestamp: Date.now(),
});
