/**
 * RTK command wrapper for adapter-managed CLI invocations.
 *
 * This installs a process-wide spawn wrapper so orchestrator-managed commands
 * can be prefixed as `rtk <command> ...args` without touching each adapter.
 */

import type { SpawnOptions } from "node:child_process";
import { createRequire, syncBuiltinESMExports } from "node:module";
import { basename } from "node:path";
import type { MercuryConfig } from "@mercury/core";

type RTKConfig = NonNullable<MercuryConfig["rtk"]>;

export interface WrappedCommand {
  command: string;
  args: string[];
}

const DEFAULT_RTK_BINARY = "rtk";
const WINDOWS_EXECUTABLE_SUFFIX = /\.(cmd|exe|bat|ps1)$/i;

const require = createRequire(import.meta.url);
const childProcess = require("node:child_process") as typeof import("node:child_process");
const originalSpawn = childProcess.spawn.bind(childProcess);

let wrapperInstalled = false;
let activeConfig: RTKConfig | undefined;

function normalizeCommandName(command: string): string {
  return basename(command).trim().toLowerCase().replace(WINDOWS_EXECUTABLE_SUFFIX, "");
}

function getRTKBinary(config?: RTKConfig): string {
  return config?.binaryPath?.trim() || DEFAULT_RTK_BINARY;
}

function shouldWrapCommand(command: string, config?: RTKConfig): boolean {
  if (!config?.enabled) {
    return false;
  }

  const normalizedCommand = normalizeCommandName(command);
  if (!normalizedCommand) {
    return false;
  }

  const normalizedRTKBinary = normalizeCommandName(getRTKBinary(config));
  if (normalizedCommand === normalizedRTKBinary) {
    return false;
  }

  return config.commands.some((candidate) => normalizeCommandName(candidate) === normalizedCommand);
}

export function wrapWithRTK(
  command: string,
  args: readonly string[] = [],
  config?: RTKConfig,
): WrappedCommand {
  if (!shouldWrapCommand(command, config)) {
    return { command, args: [...args] };
  }

  return {
    command: getRTKBinary(config),
    args: [command, ...args],
  };
}

export async function isRTKAvailable(config?: RTKConfig): Promise<boolean> {
  if (!config?.enabled) {
    return true;
  }

  const binary = getRTKBinary(config);
  const TIMEOUT_MS = 5_000;

  return new Promise<boolean>((resolve) => {
    let settled = false;
    const finish = (value: boolean) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(value);
    };

    const child = originalSpawn(binary, ["--version"], {
      stdio: "ignore",
      shell: false,
    });

    const timer = setTimeout(() => {
      try { child.kill(); } catch { /* already exited */ }
      finish(false);
    }, TIMEOUT_MS);

    child.once("error", () => finish(false));
    child.once("exit", (code) => finish(code === 0));
  });
}

function normalizeSpawnInvocation(
  argsOrOptions?: readonly string[] | SpawnOptions,
  maybeOptions?: SpawnOptions,
): { args: string[]; options?: SpawnOptions } {
  if (Array.isArray(argsOrOptions)) {
    return { args: [...argsOrOptions], options: maybeOptions };
  }

  return { args: [], options: argsOrOptions as SpawnOptions | undefined };
}

export function installRTKCommandWrapper(config?: RTKConfig): void {
  activeConfig = config?.enabled
    ? {
        ...config,
        commands: [...config.commands],
      }
    : undefined;

  if (wrapperInstalled) {
    return;
  }

  childProcess.spawn = ((
    command: string,
    argsOrOptions?: readonly string[] | SpawnOptions,
    maybeOptions?: SpawnOptions,
  ) => {
    const normalized = normalizeSpawnInvocation(argsOrOptions, maybeOptions);
    const wrapped = wrapWithRTK(command, normalized.args, activeConfig);
    if (normalized.options) {
      return originalSpawn(wrapped.command, wrapped.args, normalized.options);
    }
    return originalSpawn(wrapped.command, wrapped.args);
  }) as typeof childProcess.spawn;

  syncBuiltinESMExports();
  wrapperInstalled = true;
}
