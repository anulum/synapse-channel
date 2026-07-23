// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — authenticated governed-write transport and result mapping

import { authenticatedFetch } from "./auth";
import type {
  MessageResponseInput,
  OperatorActionResult,
  OperatorSendResult,
  OperatorTaskResult,
  SemanticResponseStatus,
  TaskDeclarationInput,
  TaskUpdateInput,
} from "./operatorActionTypes";
import {
  parseOperatorOutcome,
  validateTaskDeclaration,
  validateTaskUpdate,
} from "./operatorActionValidation";

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
    return {
      kind: "error",
      message: cause instanceof Error ? cause.message : String(cause),
    };
  }
  if (response.status === 401) return { kind: "unauthorised" };
  if (response.status === 404 || response.status === 501) return { kind: "not-armed" };

  const raw = (await response.text()).trim();
  if (response.status === 429) {
    return {
      kind: "rate-limited",
      detail: plainLine(raw, "operator rate limit exceeded"),
    };
  }
  const document = parseOperatorOutcome(parseJson(raw), action);
  if (document === null) {
    if (response.status === 503) {
      return {
        kind: "unreachable",
        detail: plainLine(raw, "dashboard could not reach the hub"),
      };
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

/** Send one semantic response tied to an exact durable message sequence. */
export async function sendOperatorResponse(
  input: MessageResponseInput,
  fetcher: typeof fetch = authenticatedFetch,
  url: string = "/message/respond",
): Promise<OperatorActionResult> {
  if (!Number.isInteger(input.messageSeq) || input.messageSeq < 1) {
    return { kind: "error", message: "Select a durable message before responding." };
  }
  const to = input.to.trim();
  if (to === "") {
    return { kind: "error", message: "The referenced sender is unavailable." };
  }
  const payload: {
    message_seq: number;
    to: string;
    status: SemanticResponseStatus;
    note?: string;
  } = { message_seq: input.messageSeq, to, status: input.status };
  const note = input.note?.trim() ?? "";
  if (note !== "") payload.note = note;
  return postOperatorAction(url, "message_response", payload, fetcher);
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
