/**
 * Role Prompt Builder — generates role-specific system prompts from ROLE_CARDS.
 *
 * Each session receives a system prompt tailored to its role, enforcing:
 * - Execution permissions (code/no-code)
 * - Delegation boundaries
 * - Input/output constraints
 * - Task scope (allowedWriteScope, docsMustNotTouch)
 * - Blind acceptance policy (for acceptance role)
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  ROLE_CARDS,
  type AgentRole,
  type TaskBundle,
  type AcceptanceBundle,
} from "@mercury/core";

export function loadRoleInstructions(
  role: AgentRole,
  basePath = process.cwd(),
): string | undefined {
  const instructionsPath = resolve(basePath, ".mercury", "roles", `${role}.md`);

  try {
    const content = readFileSync(instructionsPath, "utf-8").trim();
    return content || undefined;
  } catch (error) {
    if (
      error &&
      typeof error === "object" &&
      "code" in error &&
      (error.code === "ENOENT" || error.code === "ENOTDIR")
    ) {
      return undefined;
    }

    throw error;
  }
}

/**
 * Build a role-scoped system prompt for dev/main/research/design sessions.
 */
export function buildRoleSystemPrompt(
  role: AgentRole,
  task?: TaskBundle,
  sharedProjectContext?: string,
  roleProjectContext?: string,
  roleInstructions?: string,
): string {
  const card = ROLE_CARDS[role];
  const lines: string[] = [];

  // Role declaration
  lines.push(`# Role Assignment: ${role}`);
  lines.push(`You are assigned the **${role}** role. ${card.description}`);
  lines.push("");

  // Execution boundary
  if (!card.canExecuteCode) {
    lines.push("## Execution Constraint");
    lines.push("You are FORBIDDEN from executing code, writing files, or running commands.");
    lines.push("");
  }

  // Delegation boundary
  if (card.canDelegateToRoles.length === 0) {
    lines.push("## Delegation Constraint");
    lines.push("You are FORBIDDEN from delegating work to other agents.");
    lines.push("");
  } else {
    lines.push("## Delegation Scope");
    lines.push(`You may delegate work to: ${card.canDelegateToRoles.join(", ")}`);
    lines.push("");
  }

  // Input/output boundaries
  lines.push("## Input Boundary");
  lines.push(`You receive: ${card.inputBoundary.join(", ")}`);
  lines.push("");
  lines.push("## Output Boundary");
  lines.push(`You produce: ${card.outputBoundary.join(", ")}`);
  lines.push("");

  if (roleInstructions) {
    lines.push("## Role-Specific Instructions");
    lines.push(roleInstructions);
    lines.push("");
  }

  // Task-specific scope constraints
  if (task) {
    lines.push("## Task Scope");
    lines.push(`Task: ${task.title} [${task.taskId}]`);
    lines.push("");

    if (task.allowedWriteScope.codePaths.length > 0) {
      lines.push("### Allowed Write Paths");
      for (const p of task.allowedWriteScope.codePaths) {
        lines.push(`- ${p}`);
      }
      lines.push("");
    }

    if (task.docsMustNotTouch.length > 0) {
      lines.push("### FORBIDDEN Write Targets");
      lines.push("You MUST NOT modify the following:");
      for (const p of task.docsMustNotTouch) {
        lines.push(`- ${p}`);
      }
      lines.push("");
    }
  }

  if (sharedProjectContext) {
    lines.push("## Shared Project Context");
    lines.push(sharedProjectContext);
    lines.push("");
  }

  if (roleProjectContext) {
    lines.push("## Role-Specific Project Context");
    lines.push(roleProjectContext);
    lines.push("");
  }

  return lines.join("\n");
}

/**
 * Build a blind-review system prompt for acceptance sessions.
 * Excludes dev agent narrative (summary, evidence, residualRisks).
 */
export function buildAcceptanceRolePrompt(
  task: TaskBundle,
  acceptance: AcceptanceBundle,
  sharedProjectContext?: string,
  roleProjectContext?: string,
): string {
  const card = ROLE_CARDS.acceptance;
  const lines: string[] = [];

  // Role declaration
  lines.push(`# Role Assignment: acceptance`);
  lines.push(`You are assigned the **acceptance** role. ${card.description}`);
  lines.push("");

  // Execution permissions
  lines.push("## Execution Permissions");
  lines.push("You MAY execute code, run tests, and inspect runtime output for verification.");
  lines.push("");

  // Blind review policy
  lines.push("## BLIND REVIEW POLICY");
  lines.push("You are conducting a **blind acceptance review**.");
  lines.push("You are FORBIDDEN from referencing:");
  for (const item of acceptance.blindInputPolicy.forbidden) {
    lines.push(`- ${item}`);
  }
  lines.push("");
  lines.push("You are ALLOWED to reference:");
  for (const item of acceptance.blindInputPolicy.allowed) {
    lines.push(`- ${item}`);
  }
  lines.push("");
  lines.push("Evaluate ONLY from code, tests, and runtime output. Do NOT rely on the developer's self-assessment.");
  lines.push("");

  // Delegation constraint
  lines.push("## Delegation Constraint");
  lines.push("You are FORBIDDEN from delegating work to other agents.");
  lines.push("");

  // Task scope (non-narrative fields only)
  lines.push("## Review Scope");
  lines.push(`Task: ${task.title} [${task.taskId}]`);
  lines.push(`Acceptance: ${acceptance.acceptanceId}`);
  lines.push("");

  if (task.implementationReceipt) {
    // Only expose changedFiles and branch — NOT summary/evidence/residualRisks
    lines.push("### Changed Files");
    for (const f of task.implementationReceipt.changedFiles) {
      lines.push(`- ${f}`);
    }
    lines.push(`Branch: ${task.implementationReceipt.branch}`);
    lines.push("");
  }

  lines.push("### Definition of Done");
  for (const item of task.definitionOfDone) {
    lines.push(`- [ ] ${item}`);
  }
  lines.push("");

  if (sharedProjectContext) {
    lines.push("## Shared Project Context");
    lines.push(sharedProjectContext);
    lines.push("");
  }

  if (roleProjectContext) {
    lines.push("## Role-Specific Project Context");
    lines.push(roleProjectContext);
    lines.push("");
  }

  return lines.join("\n");
}
