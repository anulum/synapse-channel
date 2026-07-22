// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — exact conversation evidence tests

import { describe, expect, it } from "vitest";

import { conversationEvidenceFor } from "../src/lib/conversationEvidence";
import type { ConversationMessage } from "../src/lib/communications";

function message(
  seq: number,
  responseToSeq: number | null = null,
  responseStatus: ConversationMessage["responseStatus"] = null,
): ConversationMessage {
  return {
    seq,
    ts: seq,
    source: responseToSeq === null ? "alpha/one" : "beta/two",
    target: responseToSeq === null ? "beta/two" : "alpha/one",
    body: `message ${seq}`,
    delivery: "unknown",
    responseToSeq,
    responseStatus,
    responseEvidenceScope: responseToSeq === null ? null : "recipient",
  };
}

describe("conversationEvidenceFor", () => {
  it("returns null when the exact durable sequence is absent", () => {
    expect(conversationEvidenceFor([message(2)], 99)).toBeNull();
  });

  it("joins only exact semantic responses and sorts them by durable sequence", () => {
    const unrelated = message(8, 7, "declined");
    const result = conversationEvidenceFor(
      [message(6, 2, "completed"), unrelated, message(2), message(4, 2, "acknowledged")],
      2,
    );
    expect(result?.message.seq).toBe(2);
    expect(result?.responses.map((response) => response.seq)).toEqual([4, 6]);
    expect(result?.responses).not.toContain(unrelated);
  });

  it("keeps a retained message with no response distinct from missing evidence", () => {
    expect(conversationEvidenceFor([message(2)], 2)).toEqual({
      message: message(2),
      responses: [],
    });
  });
});
