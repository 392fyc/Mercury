/**
 * Mercury Orchestrator — Entry Point
 *
 * Spawned by Tauri as a sidecar process.
 * Communicates via JSON-RPC 2.0 over stdin/stdout.
 */

import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";
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

/** Migrate legacy agent config format: single `role` string to `roles` array. */
function migrateAgentConfig(agents: Record<string, unknown>[]): AgentConfig[] {
  for (const agent of agents) {
    if ("role" in agent && !("roles" in agent)) {
      agent.roles = [agent.role as string];
      delete agent.role;
    }
  }
  return agents as unknown as AgentConfig[];
}

/** Type guard: returns true if value is a non-null, non-array object. */
function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Deep-merge config overrides into defaults, preserving structure of the defaults object. */
function mergeConfigDefaults<T>(defaults: T, overrides: unknown): T {
  if (Array.isArray(defaults)) {
    return (Array.isArray(overrides) ? overrides : defaults) as T;
  }

  if (!isPlainObject(defaults) || !isPlainObject(overrides)) {
    return (overrides ?? defaults) as T;
  }

  const result: Record<string, unknown> = { ...defaults };
  for (const [key, defaultValue] of Object.entries(defaults)) {
    if (!(key in overrides)) {
      continue;
    }
    result[key] = mergeConfigDefaults(defaultValue, overrides[key]);
  }

  for (const [key, value] of Object.entries(overrides)) {
    if (!(key in result)) {
      result[key] = value;
    }
  }

  return result as T;
}

/** Resolve relative paths in config (workDir, vaultPath, obsidianBin) to absolute paths. */
function resolveConfigPaths(config: MercuryConfig, configFilePath: string): MercuryConfig {
  const configDir = dirname(configFilePath);
  return {
    ...config,
    workDir: config.workDir
      ? (isAbsolute(config.workDir) ? config.workDir : resolve(configDir, config.workDir))
      : config.workDir,
    obsidian: config.obsidian
      ? {
          ...config.obsidian,
          vaultPath: config.obsidian.vaultPath
            ? (
                isAbsolute(config.obsidian.vaultPath)
                  ? config.obsidian.vaultPath
                  : resolve(configDir, config.obsidian.vaultPath)
              )
            : config.obsidian.vaultPath,
          obsidianBin: config.obsidian.obsidianBin
            ? (
                isAbsolute(config.obsidian.obsidianBin)
                  ? config.obsidian.obsidianBin
                  : resolve(configDir, config.obsidian.obsidianBin)
              )
            : config.obsidian.obsidianBin,
        }
      : config.obsidian,
  };
}

/** Read and parse a mercury config JSON file, applying agent config migration. */
function readConfigFile(path: string): MercuryConfig {
  const raw = readFileSync(path, "utf-8");
  const config = JSON.parse(raw);
  config.agents = migrateAgentConfig(config.agents ?? []);
  return config as MercuryConfig;
}

/** Load Mercury config from project, home, or template; bootstraps a default if none found. */
function loadConfig(configPath?: string): { config: MercuryConfig; resolvedPath: string | null } {
  const projectConfigPath = configPath ?? resolve(process.cwd(), "mercury.config.json");
  const projectTemplatePath = resolve(dirname(projectConfigPath), "mercury.config.example.json");
  const homeConfigPath = resolve(process.env.HOME ?? process.env.USERPROFILE ?? "", ".mercury", "config.json");
  const defaultConfig: MercuryConfig = {
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

  let templateConfig: MercuryConfig | null = null;
  if (existsSync(projectTemplatePath)) {
    try {
      templateConfig = readConfigFile(projectTemplatePath);
      transport.log(`Loaded config template from ${projectTemplatePath}`);
    } catch {
      transport.log(`Warning: failed to load config template from ${projectTemplatePath}`);
    }
  }

  const localPaths = [configPath, projectConfigPath].filter(Boolean) as string[];
  for (const p of localPaths) {
    if (!existsSync(p)) {
      continue;
    }
    try {
      const localConfig = readConfigFile(p);
      const merged = templateConfig ? mergeConfigDefaults(templateConfig, localConfig) : localConfig;
      const resolved = resolveConfigPaths(merged, p);
      transport.log(`Loaded config from ${p} (${resolved.agents.length} agents)`);
      return { config: resolved, resolvedPath: p };
    } catch {
      // try next
    }
  }

  if (existsSync(homeConfigPath)) {
    try {
      const homeConfig = readConfigFile(homeConfigPath);
      transport.log(`Loaded config from ${homeConfigPath} (${homeConfig.agents.length} agents)`);
      return {
        config: resolveConfigPaths(homeConfig, homeConfigPath),
        resolvedPath: homeConfigPath,
      };
    } catch {
      // ignore and continue to bootstrap
    }
  }

  const bootstrappedConfig = templateConfig ?? defaultConfig;
  let writeSucceeded = false;
  try {
    writeFileSync(projectConfigPath, JSON.stringify(bootstrappedConfig, null, 2) + "\n", "utf-8");
    writeSucceeded = true;
    transport.log(`Bootstrapped local config at ${projectConfigPath}`);
  } catch (err) {
    transport.log(
      `Warning: failed to bootstrap local config at ${projectConfigPath}: ${err instanceof Error ? err.message : err}`,
    );
  }

  transport.log(templateConfig ? "No local config found, using template defaults" : "No config found, using defaults");
  return {
    config: resolveConfigPaths(bootstrappedConfig, projectConfigPath),
    resolvedPath: writeSucceeded ? projectConfigPath : null,
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
