/**
 * Agent Registry — instantiates the correct SDK adapter for each configured agent.
 */

import type { AgentConfig, AgentAdapter } from "@mercury/core";
import { ClaudeAdapter, CodexAdapter, OpencodeAdapter } from "@mercury/sdk-adapters";

export class AgentRegistry {
  private adapters = new Map<string, AgentAdapter>();
  private configs = new Map<string, AgentConfig>();

  constructor(agents: AgentConfig[]) {
    for (const config of agents) {
      this.register(config);
    }
  }

  register(config: AgentConfig): void {
    this.configs.set(config.id, config);
    this.adapters.set(config.id, this.createAdapter(config));
  }

  unregister(agentId: string): void {
    this.adapters.delete(agentId);
    this.configs.delete(agentId);
  }

  getAdapter(agentId: string): AgentAdapter {
    const adapter = this.adapters.get(agentId);
    if (!adapter) throw new Error(`Agent not found: ${agentId}`);
    return adapter;
  }

  getConfig(agentId: string): AgentConfig {
    const config = this.configs.get(agentId);
    if (!config) throw new Error(`Agent config not found: ${agentId}`);
    return config;
  }

  listAgents(): AgentConfig[] {
    return [...this.configs.values()];
  }

  getMainAgent(): AgentConfig | undefined {
    return this.listAgents().find((a) => a.role === "main");
  }

  private createAdapter(config: AgentConfig): AgentAdapter {
    switch (config.cli) {
      case "claude":
        return new ClaudeAdapter(config);
      case "codex":
        return new CodexAdapter(config);
      case "opencode":
        return new OpencodeAdapter(undefined, config);
      default:
        throw new Error(`Unknown CLI agent: ${config.cli}. Supported: claude, codex, opencode`);
    }
  }
}
