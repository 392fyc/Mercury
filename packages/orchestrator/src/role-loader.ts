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
  const raw = readFileSync(yamlPath, "utf-8");
  const data = yaml.load(raw) as Record<string, unknown>;

  return {
    role: data.role as AgentRole,
    description: data.description as string,
    canExecuteCode: data.canExecuteCode as boolean,
    canDelegateToRoles: (data.canDelegateToRoles as AgentRole[]) ?? [],
    inputBoundary: (data.inputBoundary as string[]) ?? [],
    outputBoundary: (data.outputBoundary as string[]) ?? [],
    instructions: ((data.instructions as string) ?? "").trim(),
  };
}
