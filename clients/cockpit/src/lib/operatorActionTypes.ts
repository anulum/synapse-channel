// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — immutable governed operator-action contracts

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

/** Closed semantic response vocabulary tied to an exact durable message. */
export type SemanticResponseStatus =
  | "acknowledged"
  | "in_progress"
  | "needs_input"
  | "declined"
  | "completed";

/** One exact-message semantic response request. */
export interface MessageResponseInput {
  readonly messageSeq: number;
  readonly to: string;
  readonly status: SemanticResponseStatus;
  readonly note?: string;
}

/** Existing message result retained for the palette's chat composer. */
export type OperatorSendResult =
  | { readonly kind: "sent"; readonly detail: string }
  | { readonly kind: "undelivered"; readonly detail: string }
  | { readonly kind: "not-armed" }
  | { readonly kind: "refused"; readonly reason: string }
  | { readonly kind: "error"; readonly message: string };
