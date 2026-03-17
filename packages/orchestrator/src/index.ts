/**
 * Mercury Orchestrator — Entry Point
 *
 * Spawned by Tauri as a sidecar process.
 * Communicates via JSON-RPC 2.0 over stdin/stdout.
 */

import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { MercuryConfig, AgentConfig, ObsidianConfig } from "@mercury/core";
import { RpcTransport } from "./rpc-transport.js";
import { AgentRegistry } from "./agent-registry.js";
import { Orchestrator } from "./orchestrator.js";
import { KnowledgeService } from "./knowledge-service.js";

const DEFAULT_RPC_PORT = 7654;

type JsonRpcHttpRequest = {
  jsonrpc: "2.0";
  method: string;
  params?: Record<string, unknown>;
  id: number | string | null;
};

type JsonRpcHttpResponse = {
  jsonrpc: "2.0";
  result?: unknown;
  error?: { code: number; message: string };
  id: number | string | null;
};

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

function loadConfig(configPath?: string): { config: MercuryConfig; resolvedPath: string | null } {
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
      return { config: config as MercuryConfig, resolvedPath: p };
    } catch {
      // try next
    }
  }

  transport.log("No config found, using defaults");
  return {
    config: {
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
    },
    resolvedPath: null,
  };
}

type ResolvedKBPaths = {
  tasks: string;
  acceptances: string;
  issues: string;
};

const DEFAULT_KB_PATHS: ResolvedKBPaths = {
  tasks: "tasks",
  acceptances: "acceptances",
  issues: "issues",
};

function resolveKbPaths(obsidian?: ObsidianConfig): ResolvedKBPaths {
  return {
    tasks: obsidian?.kbPaths?.tasks ?? DEFAULT_KB_PATHS.tasks,
    acceptances: obsidian?.kbPaths?.acceptances ?? DEFAULT_KB_PATHS.acceptances,
    issues: obsidian?.kbPaths?.issues ?? DEFAULT_KB_PATHS.issues,
  };
}

function parseRpcPort(raw: string | undefined): number | null {
  if (!raw) return null;
  const port = Number.parseInt(raw, 10);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    return null;
  }
  return port;
}

function resolveRpcPort(config: MercuryConfig): number {
  const envPort = parseRpcPort(process.env.MERCURY_RPC_PORT);
  if (envPort !== null) {
    return envPort;
  }

  if (process.env.MERCURY_RPC_PORT) {
    transport.log(
      `Invalid MERCURY_RPC_PORT="${process.env.MERCURY_RPC_PORT}", falling back to config/default`,
    );
  }

  if (typeof config.rpcPort === "number" && Number.isInteger(config.rpcPort)) {
    return config.rpcPort;
  }

  return DEFAULT_RPC_PORT;
}

function setCorsHeaders(res: ServerResponse): void {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
}

function sendHttpJson(
  res: ServerResponse,
  statusCode: number,
  payload: JsonRpcHttpResponse,
): void {
  setCorsHeaders(res);
  res.statusCode = statusCode;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.end(JSON.stringify(payload));
}

function readRequestBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolveBody, rejectBody) => {
    let body = "";
    req.setEncoding("utf8");
    req.on("data", (chunk) => {
      body += chunk;
    });
    req.on("end", () => resolveBody(body));
    req.on("error", rejectBody);
  });
}

function isJsonRpcParams(
  params: JsonRpcHttpRequest["params"],
): params is Record<string, unknown> | undefined {
  if (params === undefined) return true;
  return typeof params === "object" && params !== null && !Array.isArray(params);
}

function isJsonRpcRequest(value: unknown): value is JsonRpcHttpRequest {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }

  const request = value as Partial<JsonRpcHttpRequest>;
  return (
    request.jsonrpc === "2.0" &&
    typeof request.method === "string" &&
    request.method.length > 0 &&
    (typeof request.id === "number" || typeof request.id === "string" || request.id === null) &&
    isJsonRpcParams(request.params)
  );
}

function mapRpcError(err: unknown): { code: number; message: string } {
  const message = err instanceof Error ? err.message : String(err);
  if (message.startsWith("Unknown method:")) {
    return { code: -32601, message };
  }
  return { code: -32603, message };
}

function createHttpRpcServer(orchestrator: Orchestrator, port: number): Server {
  return createServer(async (req, res) => {
    setCorsHeaders(res);

    if (req.method === "OPTIONS") {
      res.statusCode = 204;
      res.end();
      return;
    }

    if (req.method !== "POST") {
      res.statusCode = 405;
      res.end("Method Not Allowed");
      return;
    }

    let body = "";
    try {
      body = await readRequestBody(req);
    } catch (err) {
      sendHttpJson(res, 500, {
        jsonrpc: "2.0",
        error: mapRpcError(err),
        id: null,
      });
      return;
    }

    let payload: unknown;
    try {
      payload = JSON.parse(body);
    } catch {
      sendHttpJson(res, 400, {
        jsonrpc: "2.0",
        error: { code: -32700, message: "Parse error" },
        id: null,
      });
      return;
    }

    if (!isJsonRpcRequest(payload)) {
      sendHttpJson(res, 400, {
        jsonrpc: "2.0",
        error: { code: -32603, message: "Invalid JSON-RPC request" },
        id: null,
      });
      return;
    }

    try {
      const result = await orchestrator.handleRpc(payload.method, payload.params ?? {});
      sendHttpJson(res, 200, {
        jsonrpc: "2.0",
        result,
        id: payload.id,
      });
    } catch (err) {
      sendHttpJson(res, 500, {
        jsonrpc: "2.0",
        error: mapRpcError(err),
        id: payload.id,
      });
    }
  }).listen(port, "127.0.0.1", () => {
    transport.log(`HTTP JSON-RPC listening on http://127.0.0.1:${port}`);
  });
}

function wireShutdown(server: Server): void {
  const originalExit = process.exit.bind(process);
  let shuttingDown = false;

  const closeServer = (reason: string, exitCode?: number) => {
    if (shuttingDown) {
      if (exitCode !== undefined) {
        originalExit(exitCode);
      }
      return;
    }
    shuttingDown = true;
    transport.log(`Shutting down HTTP JSON-RPC server (${reason})`);
    server.close((err) => {
      process.exit = originalExit;
      if (err) {
        transport.log(`HTTP JSON-RPC shutdown error: ${err instanceof Error ? err.message : err}`);
      }
      if (exitCode !== undefined) {
        originalExit(err ? 1 : exitCode);
      }
    });
  };

  // RpcTransport exits the process when stdin closes; intercept that path so the HTTP server closes first.
  process.exit = ((code?: number) => {
    closeServer("process.exit", code ?? 0);
    return undefined as never;
  }) as typeof process.exit;

  process.once("SIGINT", () => closeServer("SIGINT", 0));
  process.once("SIGTERM", () => closeServer("SIGTERM", 0));
  process.once("beforeExit", () => closeServer("beforeExit"));
  process.once("exit", () => {
    if (server.listening) {
      server.close();
    }
  });
}

function startTransports(orchestrator: Orchestrator, registry: AgentRegistry, config: MercuryConfig): void {
  transport.start((method, params) => orchestrator.handleRpc(method, params));
  const httpServer = createHttpRpcServer(orchestrator, resolveRpcPort(config));
  wireShutdown(httpServer);
  transport.sendNotification("ready", {
    agents: registry.listAgents().map((a) => a.id),
    timestamp: Date.now(),
  });
}

// Bootstrap
const transport = new RpcTransport();
const configPath = process.argv[2];
const { config, resolvedPath: configFilePath } = loadConfig(configPath);

if (config.workDir) {
  process.chdir(resolve(process.cwd(), config.workDir));
}

const registry = new AgentRegistry(config.agents);
const orchestrator = new Orchestrator(registry, transport);

// Store full config for get_config/update_config RPC (with file path for persistence)
orchestrator.setProjectConfig(config, configFilePath);

// Enable session persistence
orchestrator.setPersistencePath(process.cwd());

// Initialize optional Knowledge Service (Obsidian CLI)
if (config.obsidian?.enabled) {
  const kb = new KnowledgeService(config.obsidian);
  (kb as KnowledgeService & { kbPaths?: ResolvedKBPaths }).kbPaths = resolveKbPaths(config.obsidian);
  orchestrator.setKnowledgeService(kb);
  transport.log(`Knowledge service enabled: vault="${config.obsidian.vaultName}"`);
} else {
  transport.log("Knowledge service disabled (obsidian not configured or not enabled)");
}

// Wire agent config lookup for Agents First assignee.model (works with or without KB)
orchestrator.setAgentConfigLookup();

// Rehydrate task state from KB (no-op if KB disabled), then start RPC + signal ready
orchestrator.init().then(() => {
  startTransports(orchestrator, registry, config);
}).catch((err) => {
  transport.log(`Init warning: ${err instanceof Error ? err.message : err}`);
  startTransports(orchestrator, registry, config);
});
