/**
 * Role Loader — reads .mercury/roles/{role}.yaml at runtime and returns typed RoleCard + instructions.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import yaml from "js-yaml";
import type { AgentRole, RoleCard } from "@mercury/core";

export interface LoadedRoleCard extends RoleCard {
  instructions: string;
}

/**
 * Load a role definition from .mercury/roles/{role}.yaml.
 * Returns RoleCard fields plus the instructions text block.
 */
export function loadRoleCard(
  role: AgentRole,
  basePath = process.cwd(),
): LoadedRoleCard {
  const yamlPath = resolve(basePath, ".mercury", "roles", `${role}.yaml`);

  let raw: string;
  try {
    raw = readFileSync(yamlPath, "utf-8");
  } catch (err) {
    throw new Error(
      `Failed to read role definition for "${role}" at ${yamlPath}: ${err instanceof Error ? err.message : String(err)}`,
    );
  }

  let parsed: unknown;
  try {
    parsed = yaml.load(raw);
  } catch (err) {
    throw new Error(
      `Failed to parse YAML for role "${role}" at ${yamlPath}: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
  if (parsed == null || typeof parsed !== "object") {
    throw new Error(
      `Invalid role definition for "${role}" at ${yamlPath}: expected a YAML mapping, got ${parsed === null ? "null" : typeof parsed}`,
    );
  }
  const data = parsed as Record<string, unknown>;

  return {
    role: data.role as AgentRole,
    description: (data.description as string) ?? "",
    canExecuteCode: Boolean(data.canExecuteCode),
    canDelegateToRoles: (data.canDelegateToRoles as AgentRole[]) ?? [],
    inputBoundary: (data.inputBoundary as string[]) ?? [],
    outputBoundary: (data.outputBoundary as string[]) ?? [],
    instructions: ((data.instructions as string) ?? "").trim(),
  };
}
