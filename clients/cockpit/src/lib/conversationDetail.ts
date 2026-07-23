// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — selected pairwise conversation projection

import type { CockpitEvent } from "../types";
import { eventsInWindow, type TimeWindow } from "./brush";
import {
  communicationText,
  finiteCommunicationNumber,
  isChatEvent,
  receiptOutcome,
  receiptOutcomeRank,
  type ReceiptOutcome,
} from "./communicationEvidence";

/** Semantic response state explicitly authored by the recipient or operator. */
export type SemanticResponseStatus = "acknowledged" | "in_progress" | "needs_input" | "declined" | "completed";

/** Principal class that authored a semantic response. */
export type SemanticResponseEvidenceScope = "recipient" | "operator_commentary";

/** One body-bearing row shown only after an exact pair is selected. */
export interface ConversationMessage {
  readonly seq: number;
  readonly ts: number;
  readonly source: string;
  readonly target: string;
  readonly body: string;
  readonly delivery: ReceiptOutcome | "unknown";
  readonly responseToSeq: number | null;
  readonly responseStatus: SemanticResponseStatus | null;
  readonly responseEvidenceScope: SemanticResponseEvidenceScope | null;
}

const SEMANTIC_RESPONSE_STATUSES = new Set<SemanticResponseStatus>([
  "acknowledged",
  "in_progress",
  "needs_input",
  "declined",
  "completed",
]);
const SEMANTIC_RESPONSE_EVIDENCE_SCOPES = new Set<SemanticResponseEvidenceScope>([
  "recipient",
  "operator_commentary",
]);
const CONVERSATION_BODY_LIMIT = 500;

function responseStatus(value: unknown): SemanticResponseStatus | null {
  return typeof value === "string" && SEMANTIC_RESPONSE_STATUSES.has(value as SemanticResponseStatus)
    ? (value as SemanticResponseStatus)
    : null;
}

function responseEvidenceScope(value: unknown): SemanticResponseEvidenceScope | null {
  return typeof value === "string" && SEMANTIC_RESPONSE_EVIDENCE_SCOPES.has(value as SemanticResponseEvidenceScope)
    ? (value as SemanticResponseEvidenceScope)
    : null;
}

function messageBody(value: unknown): string {
  if (typeof value !== "string") return "";
  return value.length > CONVERSATION_BODY_LIMIT ? `${value.slice(0, CONVERSATION_BODY_LIMIT)}…` : value;
}

function conversationOutcomes(events: readonly CockpitEvent[]): ReadonlyMap<number, ReceiptOutcome> {
  const outcomes = new Map<number, ReceiptOutcome>();
  for (const event of events) {
    const outcome = receiptOutcome(event);
    const messageSeq = finiteCommunicationNumber(event.payload?.["message_seq"]);
    if (outcome === null || messageSeq === null) continue;
    const prior = outcomes.get(messageSeq);
    if (prior === undefined || receiptOutcomeRank(outcome) >= receiptOutcomeRank(prior)) {
      outcomes.set(messageSeq, outcome);
    }
  }
  return outcomes;
}

/**
 * Build a bounded pairwise timeline only after an edge is selected. Unlike the
 * graph projection, this detail intentionally carries authenticated body text.
 */
export function deriveConversationDetail(
  events: readonly CockpitEvent[],
  source: string,
  target: string,
  window: TimeWindow | null = null,
  limit = 40,
): ConversationMessage[] {
  const scoped = eventsInWindow(events, window);
  const outcomes = conversationOutcomes(scoped);
  return scoped
    .filter((event) => {
      if (!isChatEvent(event)) return false;
      const sender = communicationText(event.payload?.["sender"]);
      const recipient = communicationText(event.payload?.["target"]);
      return (sender === source && recipient === target) || (sender === target && recipient === source);
    })
    .map((event): ConversationMessage => {
      const responseTo = finiteCommunicationNumber(event.payload?.["response_to_seq"]);
      return {
        seq: event.seq,
        ts: event.ts,
        source: communicationText(event.payload?.["sender"]),
        target: communicationText(event.payload?.["target"]),
        body: messageBody(event.payload?.["payload"]),
        delivery: outcomes.get(event.seq) ?? "unknown",
        responseToSeq: responseTo !== null && responseTo > 0 ? responseTo : null,
        responseStatus: responseStatus(event.payload?.["response_status"]),
        responseEvidenceScope: responseEvidenceScope(event.payload?.["response_evidence_scope"]),
      };
    })
    .sort((left, right) => right.ts - left.ts || right.seq - left.seq)
    .slice(0, Math.max(1, limit));
}
