/**
 * Role Loader — reads .mercury/roles/{role}.yaml at runtime and returns typed RoleCard + instructions.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import yaml from "js-yaml";
import type { AgentRole, RoleCard } from "@mercury/core";

/** Runtime-loaded role card including the instructions text block from YAML. */
export interface LoadedRoleCard extends RoleCard {
  instructions: string;
}

const VALID_ROLES: ReadonlySet<string> = new Set(["main", "dev", "acceptance", "research", "design"]);

function isAgentRole(value: unknown): value is AgentRole {
  return typeof value === "string" && VALID_ROLES.has(value);
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((v): v is string => typeof v === "string")
    .map((v) => v.trim())
    .filter((v) => v.length > 0);
}

function asRoleArray(value: unknown): AgentRole[] {
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is AgentRole => isAgentRole(v));
}

/**
 * Load a role definition from .mercury/roles/{role}.yaml.
 * Returns RoleCard fields plus the instructions text block.
 * Validates all fields at runtime — throws descriptive errors on missing/invalid data.
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
    parsed = yaml.load(raw, { schema: yaml.JSON_SCHEMA });
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

  // Validate required 'role' field
  if (!isAgentRole(data.role)) {
    throw new Error(
      `Invalid role field in ${yamlPath}: expected one of [${[...VALID_ROLES].join(", ")}], got "${String(data.role)}"`,
    );
  }

  return {
    role: data.role,
    description: typeof data.description === "string" ? data.description : "",
    canExecuteCode: Boolean(data.canExecuteCode),
    canDelegateToRoles: asRoleArray(data.canDelegateToRoles),
    inputBoundary: asStringArray(data.inputBoundary),
    outputBoundary: asStringArray(data.outputBoundary),
    instructions: (typeof data.instructions === "string" ? data.instructions : "").trim(),
  };
}
