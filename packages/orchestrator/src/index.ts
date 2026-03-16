/**
 * Mercury Orchestrator — Entry Point
 *
 * Spawned by Tauri as a sidecar process.
 * Communicates via JSON-RPC 2.0 over stdin/stdout.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { MercuryConfig, AgentConfig } from "@mercury/core";
import { RpcTransport } from "./rpc-transport.js";
import { AgentRegistry } from "./agent-registry.js";
import { Orchestrator } from "./orchestrator.js";
import { KnowledgeService } from "./knowledge-service.js";

/** Migrate legacy config: `role: "main"` → `roles: ["main"]` */
function migrateAgentConfig(agents: Record<string, unknown>[]): AgentConfig[] {
  for (const agent of agents) {
    if ("role" in agent && !("roles" in agent)) {
      agent.roles = [agent.role as string];
      delete agent.role;
    }
  }
  return agents as unknown as AgentConfig[];
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
      const config = JSON.parse(raw);
      config.agents = migrateAgentConfig(config.agents ?? []);
      transport.log(`Loaded config from ${p} (${config.agents.length} agents)`);
      return config as MercuryConfig;
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
        roles: ["main"],
        integration: "sdk",
        capabilities: ["code", "review", "orchestration"],
        restrictions: [],
        maxConcurrentSessions: 3,
      },
      {
        id: "codex-cli",
        displayName: "Codex CLI",
        cli: "codex",
        roles: ["dev"],
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

// Store full config for get_config/update_config RPC
orchestrator.setProjectConfig(config);

// Enable session persistence
orchestrator.setPersistencePath(process.cwd());

// Initialize optional Knowledge Service (Obsidian CLI)
if (config.obsidian?.enabled) {
  const kb = new KnowledgeService(config.obsidian);
  orchestrator.setKnowledgeService(kb);
  transport.log(`Knowledge service enabled: vault="${config.obsidian.vaultName}"`);
} else {
  transport.log("Knowledge service disabled (obsidian not configured or not enabled)");
}

// Wire agent config lookup for Agents First assignee.model (works with or without KB)
orchestrator.setAgentConfigLookup();

// Rehydrate task state from KB (no-op if KB disabled), then start RPC + signal ready
orchestrator.init().then(() => {
  transport.start((method, params) => orchestrator.handleRpc(method, params));
  transport.sendNotification("ready", {
    agents: registry.listAgents().map((a) => a.id),
    timestamp: Date.now(),
  });
}).catch((err) => {
  transport.log(`Init warning: ${err instanceof Error ? err.message : err}`);
  transport.start((method, params) => orchestrator.handleRpc(method, params));
  transport.sendNotification("ready", {
    agents: registry.listAgents().map((a) => a.id),
    timestamp: Date.now(),
  });
});
