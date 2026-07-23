// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — communication-event evidence contract tests

import { describe, expect, it } from "vitest";

import {
  communicationText,
  finiteCommunicationNumber,
  isChatEvent,
  receiptOutcome,
  receiptOutcomeRank,
} from "../src/lib/communicationEvidence";
import type { CockpitEvent } from "../src/types";

function event(label: string, payload?: Record<string, unknown>): CockpitEvent {
  return {
    seq: 1,
    ts: 1,
    kind: "chat",
    lane: "task",
    severity: 0.2,
    actor: "",
    label,
    taskId: "",
    ...(payload === undefined ? {} : { payload }),
  };
}

describe("communication evidence normalisation", () => {
  it("narrows strings and finite sequence values without coercion", () => {
    expect(communicationText("  alpha/one  ")).toBe("alpha/one");
    expect(communicationText(7)).toBe("");
    expect(finiteCommunicationNumber(7)).toBe(7);
    expect(finiteCommunicationNumber(Number.POSITIVE_INFINITY)).toBeNull();
    expect(finiteCommunicationNumber("7")).toBeNull();
  });

  it("recognises only routed chat evidence", () => {
    expect(isChatEvent(event("absent"))).toBe(false);
    expect(isChatEvent(event("chat", { sender: "alpha/one", target: "beta/two", type: "chat" }))).toBe(true);
    expect(isChatEvent(event("body", { sender: "alpha/one", target: "beta/two", payload: "hello" }))).toBe(true);
    expect(isChatEvent(event("missing sender", { sender: 7, target: "beta/two", type: "chat" }))).toBe(false);
    expect(isChatEvent(event("wrong shape", { sender: "alpha/one", target: "beta/two" }))).toBe(false);
  });

  it("projects every supported receipt finality and leaves other events inert", () => {
    expect(receiptOutcome(event("delivery_receipt_immediate", { delivered: true }))).toBe("delivered");
    expect(receiptOutcome(event("delivery_receipt_immediate", { delivered: false }))).toBe("failed");
    expect(receiptOutcome(event("delivery_receipt_deferred", { deferred: true }))).toBe("deferred");
    expect(receiptOutcome(event("delivery_receipt_other", { deferred: true }))).toBe("deferred");
    expect(receiptOutcome(event("delivery_receipt_expired", {}))).toBe("failed");
    expect(receiptOutcome(event("delivery_receipt_other", { expired: true }))).toBe("failed");
    expect(receiptOutcome(event("delivery_receipt_other", {}))).toBeNull();
    expect(receiptOutcome(event("chat", { delivered: true }))).toBeNull();
    expect(receiptOutcome(event("delivery_receipt_missing"))).toBeNull();
  });

  it("orders failure above deferral above delivery", () => {
    expect(receiptOutcomeRank("failed")).toBe(3);
    expect(receiptOutcomeRank("deferred")).toBe(2);
    expect(receiptOutcomeRank("delivered")).toBe(1);
  });
});
