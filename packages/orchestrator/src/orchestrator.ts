/**
 * Mercury Orchestrator — core class managing agent sessions, prompts, and event flow.
 */

import { EventBus } from "@mercury/core";
import type { AgentConfig, SessionInfo, AgentMessage } from "@mercury/core";
import { AgentRegistry } from "./agent-registry.js";
import type { RpcTransport } from "./rpc-transport.js";

export class Orchestrator {
  private bus: EventBus;
  private registry: AgentRegistry;
  private transport: RpcTransport;
  private sessions = new Map<string, SessionInfo>();
  private agentSessions = new Map<string, string>(); // agentId → active sessionId

  constructor(
    registry: AgentRegistry,
    transport: RpcTransport,
    bus?: EventBus,
  ) {
    this.registry = registry;
    this.transport = transport;
    this.bus = bus ?? new EventBus();

    // Forward all EventBus events as RPC notifications
    this.bus.on("*", (event) => {
      this.transport.sendNotification("mercury_event", {
        id: event.id,
        type: event.type,
        timestamp: event.timestamp,
        agentId: event.agentId,
        sessionId: event.sessionId,
        payload: event.payload as Record<string, unknown>,
        parentEventId: event.parentEventId,
      });
    });
  }

  async handleRpc(
    method: string,
    params: Record<string, unknown>,
  ): Promise<unknown> {
    switch (method) {
      case "get_agents":
        return this.getAgents();
      case "start_session":
        return this.startSession(params.agentId as string);
      case "send_prompt":
        return this.sendPrompt(
          params.agentId as string,
          params.prompt as string,
        );
      case "stop_session":
        return this.stopSession(
          params.agentId as string,
          params.sessionId as string,
        );
      case "configure_agent":
        return this.configureAgent(params.config as AgentConfig);
      case "dispatch_task":
        return this.dispatchTask(
          params.fromAgentId as string,
          params.toAgentId as string,
          params.prompt as string,
        );
      case "ping":
        return { pong: true, timestamp: Date.now() };
      default:
        throw new Error(`Unknown method: ${method}`);
    }
  }

  private getAgents(): AgentConfig[] {
    return this.registry.listAgents();
  }

  private async startSession(agentId: string): Promise<SessionInfo> {
    const adapter = this.registry.getAdapter(agentId);
    const session = await adapter.startSession(process.cwd());

    this.sessions.set(session.sessionId, session);
    this.agentSessions.set(agentId, session.sessionId);

    this.bus.emit("agent.session.start", agentId, session.sessionId, {
      role: this.registry.getConfig(agentId).role,
    });

    return session;
  }

  private async sendPrompt(
    agentId: string,
    prompt: string,
  ): Promise<{ sessionId: string }> {
    const adapter = this.registry.getAdapter(agentId);

    // Auto-start session if none exists
    let sessionId = this.agentSessions.get(agentId);
    if (!sessionId) {
      const session = await this.startSession(agentId);
      sessionId = session.sessionId;
    }

    this.bus.emit("agent.message.send", agentId, sessionId, {
      prompt: prompt.slice(0, 200),
    });

    // Stream messages asynchronously
    this.streamMessages(adapter, agentId, sessionId, prompt);

    return { sessionId };
  }

  private async streamMessages(
    adapter: ReturnType<AgentRegistry["getAdapter"]>,
    agentId: string,
    sessionId: string,
    prompt: string,
  ): Promise<void> {
    try {
      for await (const message of adapter.sendPrompt(sessionId, prompt)) {
        this.bus.emit("agent.message.receive", agentId, sessionId, {
          contentPreview: message.content.slice(0, 200),
        });

        this.transport.sendNotification("agent_message", {
          agentId,
          sessionId,
          message: {
            role: message.role,
            content: message.content,
            timestamp: message.timestamp,
            metadata: message.metadata,
          },
        });
      }

      // Signal stream complete
      this.transport.sendNotification("agent_stream_end", {
        agentId,
        sessionId,
      });
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      this.bus.emit("agent.error", agentId, sessionId, { error: errorMsg });
      this.transport.sendNotification("agent_error", {
        agentId,
        sessionId,
        error: errorMsg,
      });
    }
  }

  private async stopSession(
    agentId: string,
    sessionId: string,
  ): Promise<void> {
    const adapter = this.registry.getAdapter(agentId);
    await adapter.endSession(sessionId);

    this.sessions.delete(sessionId);
    if (this.agentSessions.get(agentId) === sessionId) {
      this.agentSessions.delete(agentId);
    }

    this.bus.emit("agent.session.end", agentId, sessionId, {});
  }

  private configureAgent(config: AgentConfig): { ok: true } {
    this.registry.register(config);
    return { ok: true };
  }

  private async dispatchTask(
    fromAgentId: string,
    toAgentId: string,
    prompt: string,
  ): Promise<{ sessionId: string; taskId: string }> {
    const fromSessionId = this.agentSessions.get(fromAgentId) ?? "orchestrator";
    const taskId = `TASK-${Date.now()}`;

    const taskEvent = this.bus.emit(
      "orchestrator.task.dispatch",
      fromAgentId,
      fromSessionId,
      {
        taskId,
        assignedTo: toAgentId,
        prompt: prompt.slice(0, 200),
      },
    );

    // Start sub-agent session and send prompt
    const result = await this.sendPrompt(toAgentId, prompt);

    return { sessionId: result.sessionId, taskId };
  }
}
