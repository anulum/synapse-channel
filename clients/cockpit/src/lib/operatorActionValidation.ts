// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — strict operator outcome and local task validation

import type {
  OperatorOutcomeDocument,
  TaskDeclarationInput,
  TaskUpdateInput,
} from "./operatorActionTypes";

/** Narrow an untrusted response body to the expected action's strict document. */
export function parseOperatorOutcome(
  raw: unknown,
  expectedAction: string,
): OperatorOutcomeDocument | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const record = raw as Record<string, unknown>;
  if (
    record["action"] !== expectedAction ||
    typeof record["status"] !== "string" ||
    record["status"].trim() === "" ||
    typeof record["detail"] !== "string" ||
    typeof record["ok"] !== "boolean"
  ) {
    return null;
  }
  return {
    action: expectedAction,
    status: record["status"],
    detail: record["detail"],
    ok: record["ok"],
  };
}

/** Split comma/newline dependency input into a stable de-duplicated id list. */
export function parseDependencyIds(raw: string): string[] {
  const seen = new Set<string>();
  for (const part of raw.split(/[,\n]/u)) {
    const id = part.trim();
    if (id !== "") seen.add(id);
  }
  return [...seen];
}

/** Return a local task-declaration validation error, or null when valid. */
export function validateTaskDeclaration(input: TaskDeclarationInput): string | null {
  const id = input.id.trim();
  if (id === "") return "Task id is required.";
  if (input.title.trim() === "") return "Task title is required.";
  if (input.dependsOn.some((dependency) => dependency.trim() === id)) {
    return "A task cannot depend on itself.";
  }
  return null;
}

/** Return a local task-update validation error, or null when valid. */
export function validateTaskUpdate(input: TaskUpdateInput): string | null {
  if (input.id.trim() === "") return "Task id is required.";
  const status = input.status?.trim() ?? "";
  const note = input.note?.trim() ?? "";
  if (status === "" && note === "") return "Add a status or note before updating the task.";
  return null;
}
