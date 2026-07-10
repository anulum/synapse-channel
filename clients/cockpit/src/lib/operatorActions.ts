// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — strict governed operator-action client

import { authenticatedFetch } from "./auth";

/** The dashboard's governed write response. Extra action-specific fields are ignored. */
export interface OperatorOutcomeDocument {
  readonly action: string;
  readonly status: string;
  readonly detail: string;
  readonly ok: boolean;
}

/** One honest result from the dashboard boundary. HTTP 200 alone is never acceptance. */
export type OperatorActionResult =
  | { readonly kind: "accepted"; readonly status: string; readonly detail: string }
  | { readonly kind: "denied"; readonly detail: string }
  | { readonly kind: "rejected"; readonly detail: string }
  | { readonly kind: "unreachable"; readonly detail: string }
  | { readonly kind: "not-armed" }
  | { readonly kind: "unauthorised" }
  | { readonly kind: "rate-limited"; readonly detail: string }
  | { readonly kind: "error"; readonly message: string };

/** A task action can fail local validation before any request leaves the tab. */
export type OperatorTaskResult =
  | OperatorActionResult
  | { readonly kind: "invalid"; readonly message: string };

/** Input accepted by the task-declaration action. */
export interface TaskDeclarationInput {
  readonly id: string;
  readonly title: string;
  readonly dependsOn: readonly string[];
}

/** Input accepted by the task-update action. */
export interface TaskUpdateInput {
  readonly id: string;
  readonly status?: string;
  readonly note?: string;
}

/** Existing message result retained for the palette's chat composer. */
export type OperatorSendResult =
  | { readonly kind: "sent"; readonly detail: string }
  | { readonly kind: "undelivered"; readonly detail: string }
  | { readonly kind: "not-armed" }
  | { readonly kind: "refused"; readonly reason: string }
  | { readonly kind: "error"; readonly message: string };

interface TaskDeclarationPayload {
  readonly id: string;
  readonly title: string;
  readonly depends_on: readonly string[];
}

interface TaskUpdatePayload {
  readonly id: string;
  status?: string;
  note?: string;
}

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

function plainLine(raw: string, fallback: string): string {
  const line = raw.trim();
  return line === "" || line.startsWith("<") || line.length > 180 ? fallback : line;
}

function parseJson(raw: string): unknown {
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return null;
  }
}

async function postOperatorAction(
  url: string,
  action: string,
  body: object,
  fetcher: typeof fetch,
): Promise<OperatorActionResult> {
  let response: Response;
  try {
    response = await fetcher(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (cause) {
    return { kind: "error", message: cause instanceof Error ? cause.message : String(cause) };
  }
  if (response.status === 401) return { kind: "unauthorised" };
  if (response.status === 404 || response.status === 501) return { kind: "not-armed" };

  const raw = (await response.text()).trim();
  if (response.status === 429) {
    return { kind: "rate-limited", detail: plainLine(raw, "operator rate limit exceeded") };
  }
  const document = parseOperatorOutcome(parseJson(raw), action);
  if (document === null) {
    if (response.status === 503) {
      return { kind: "unreachable", detail: plainLine(raw, "dashboard could not reach the hub") };
    }
    return {
      kind: "error",
      message: `dashboard returned ${response.status} without a valid ${action} outcome`,
    };
  }
  const fallback = `dashboard returned ${response.status}`;
  if (document.status === "denied") {
    return { kind: "denied", detail: plainLine(document.detail, fallback) };
  }
  if (document.status === "rejected") {
    return { kind: "rejected", detail: plainLine(document.detail, fallback) };
  }
  if (document.status === "unreachable") {
    return { kind: "unreachable", detail: plainLine(document.detail, fallback) };
  }
  if (!response.ok) {
    return {
      kind: "error",
      message: `dashboard returned ${response.status} with a success-shaped ${action} outcome`,
    };
  }
  if (!document.ok) {
    return { kind: "error", message: `dashboard reported unknown outcome '${document.status}'` };
  }
  return {
    kind: "accepted",
    status: document.status,
    detail: plainLine(document.detail, ""),
  };
}

/** Declare one task through the governed dashboard route. */
export async function declareOperatorTask(
  input: TaskDeclarationInput,
  fetcher: typeof fetch = authenticatedFetch,
  url: string = "/task",
): Promise<OperatorTaskResult> {
  const error = validateTaskDeclaration(input);
  if (error !== null) return { kind: "invalid", message: error };
  const id = input.id.trim();
  const payload: TaskDeclarationPayload = {
    id,
    title: input.title.trim(),
    depends_on: [...new Set(input.dependsOn.map((dependency) => dependency.trim()).filter(Boolean))],
  };
  return postOperatorAction(url, "task", payload, fetcher);
}

/** Update one task through the governed dashboard route. */
export async function updateOperatorTask(
  input: TaskUpdateInput,
  fetcher: typeof fetch = authenticatedFetch,
  url: string = "/task/update",
): Promise<OperatorTaskResult> {
  const error = validateTaskUpdate(input);
  if (error !== null) return { kind: "invalid", message: error };
  const payload: TaskUpdatePayload = { id: input.id.trim() };
  const status = input.status?.trim() ?? "";
  const note = input.note?.trim() ?? "";
  if (status !== "") payload.status = status;
  if (note !== "") payload.note = note;
  return postOperatorAction(url, "task_update", payload, fetcher);
}

/** Relay one chat message through the governed dashboard route. */
export async function sendOperatorMessage(
  to: string,
  text: string,
  fetcher: typeof fetch = authenticatedFetch,
  url: string = "/message",
): Promise<OperatorSendResult> {
  const result = await postOperatorAction(url, "message", { to, text }, fetcher);
  if (result.kind === "accepted") {
    return result.status === "undelivered"
      ? { kind: "undelivered", detail: result.detail }
      : { kind: "sent", detail: result.detail };
  }
  if (result.kind === "not-armed") return result;
  if (result.kind === "error") return result;
  if (result.kind === "unauthorised") {
    return { kind: "refused", reason: "dashboard bearer was refused" };
  }
  return {
    kind: "refused",
    reason: result.kind === "rate-limited" ? `rate limited: ${result.detail}` : result.detail,
  };
}
