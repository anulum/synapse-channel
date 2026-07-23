// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — communication-event evidence normalisation

import type { CockpitEvent } from "../types";

/** Delivery outcome accepted from retained receipt evidence. */
export type ReceiptOutcome = "delivered" | "deferred" | "failed";

/** Return a trimmed string or the empty evidence value. */
export function communicationText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/** Narrow an untrusted value to a finite number. */
export function finiteCommunicationNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Whether a retained event has the required chat routing evidence. */
export function isChatEvent(event: CockpitEvent): boolean {
  const payload = event.payload;
  if (payload === undefined) return false;
  return (
    communicationText(payload["sender"]) !== "" &&
    communicationText(payload["target"]) !== "" &&
    (payload["type"] === "chat" || Object.hasOwn(payload, "payload"))
  );
}

/** Project one retained delivery-receipt event into its finality class. */
export function receiptOutcome(event: CockpitEvent): ReceiptOutcome | null {
  const payload = event.payload;
  if (payload === undefined || !event.label.startsWith("delivery_receipt_")) return null;
  if (event.label === "delivery_receipt_expired" || payload["expired"] === true) return "failed";
  if (event.label === "delivery_receipt_deferred" || payload["deferred"] === true) return "deferred";
  if (event.label === "delivery_receipt_immediate") {
    return payload["delivered"] === true ? "delivered" : "failed";
  }
  return null;
}

/** Order receipt evidence so later weaker frames cannot erase final failure. */
export function receiptOutcomeRank(outcome: ReceiptOutcome): number {
  return outcome === "failed" ? 3 : outcome === "deferred" ? 2 : 1;
}
