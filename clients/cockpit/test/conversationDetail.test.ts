// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — selected conversation projection tests

import { describe, expect, it } from "vitest";

import { deriveConversationDetail } from "../src/lib/conversationDetail";
import type { CockpitEvent } from "../src/types";

function event(seq: number, label: string, payload: Record<string, unknown>, ts = seq): CockpitEvent {
  return {
    seq,
    ts,
    kind: "chat",
    lane: "task",
    severity: 0.2,
    actor: typeof payload["sender"] === "string" ? payload["sender"] : "",
    label,
    taskId: "",
    payload,
  };
}

const CHAT = event(10, "chat", {
  sender: "alpha/one",
  target: "beta/two",
  type: "chat",
  payload: "secret body",
});

describe("deriveConversationDetail", () => {
  it("reveals bodies only for a selected pair and correlates semantic responses", () => {
    const response = event(15, "ack", {
      sender: "beta/two",
      target: "alpha/one",
      type: "chat",
      payload: "Acknowledged.",
      response_to_seq: 10,
      response_status: "acknowledged",
      response_evidence_scope: "recipient",
    });
    const receipt = event(16, "delivery_receipt_immediate", { message_seq: 15, delivered: true });
    const detail = deriveConversationDetail([receipt, response, CHAT], "alpha/one", "beta/two");
    expect(detail).toHaveLength(2);
    expect(detail[0]).toMatchObject({
      seq: 15,
      body: "Acknowledged.",
      delivery: "delivered",
      responseToSeq: 10,
      responseStatus: "acknowledged",
      responseEvidenceScope: "recipient",
    });
    expect(detail[1]).toMatchObject({ seq: 10, body: "secret body", delivery: "unknown" });
    expect(deriveConversationDetail([CHAT], "alpha/one", "other/three")).toEqual([]);
  });

  it("bounds bodies and rejects invalid semantic metadata without losing pair order", () => {
    const longBody = "x".repeat(501);
    const invalid = event(21, "chat", {
      sender: "alpha/one",
      target: "beta/two",
      payload: 7,
      response_to_seq: -1,
      response_status: "invented",
      response_evidence_scope: 7,
    }, 30);
    const bounded = event(22, "chat", {
      sender: "beta/two",
      target: "alpha/one",
      payload: longBody,
      response_status: 7,
      response_evidence_scope: "invented",
    }, 30);
    const detail = deriveConversationDetail(
      [
        event(23, "delivery_receipt_deferred", { message_seq: 22, deferred: true }),
        event(24, "delivery_receipt_immediate", { message_seq: 22, delivered: true }),
        event(25, "delivery_receipt_immediate", { message_seq: "bad", delivered: true }),
        invalid,
        bounded,
      ],
      "alpha/one",
      "beta/two",
      null,
      0,
    );
    expect(detail).toHaveLength(1);
    expect(detail[0]).toMatchObject({
      seq: 22,
      body: `${"x".repeat(500)}…`,
      delivery: "deferred",
      responseToSeq: null,
      responseStatus: null,
      responseEvidenceScope: null,
    });
  });

  it("applies the selected time window and ignores events without chat evidence", () => {
    const absentPayload: CockpitEvent = {
      seq: 30,
      ts: 30,
      kind: "chat",
      lane: "task",
      severity: 0.2,
      actor: "",
      label: "absent",
      taskId: "",
    };
    const inside = event(31, "chat", {
      sender: "alpha/one",
      target: "beta/two",
      type: "chat",
      payload: "inside",
      response_to_seq: 0,
      response_status: "completed",
      response_evidence_scope: "operator_commentary",
    }, 31);
    expect(
      deriveConversationDetail([absentPayload, CHAT, inside], "alpha/one", "beta/two", { fromTs: 30, toTs: 32 }),
    ).toMatchObject([
      {
        seq: 31,
        body: "inside",
        responseToSeq: null,
        responseStatus: "completed",
        responseEvidenceScope: "operator_commentary",
      },
    ]);
  });
});
