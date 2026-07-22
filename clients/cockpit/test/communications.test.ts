// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — communication projection and stable-layout tests

import { describe, expect, it } from "vitest";

import {
  deriveCommunicationModel,
  deriveConversationDetail,
  layoutCommunicationWeb,
  matrixIdentities,
  projectOf,
} from "../src/lib/communications";
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

const CHAT = event(10, "body is deliberately ignored", {
  sender: "alpha/one",
  target: "beta/two",
  type: "chat",
  payload: "secret body",
});

describe("deriveCommunicationModel", () => {
  it("projects metadata, correlates receipts, and never carries message bodies", () => {
    const model = deriveCommunicationModel([
      event(12, "delivery_receipt_immediate", {
        sender: "alpha/one",
        target: "beta/two",
        message_seq: 10,
        delivered: true,
        deferred: false,
      }),
      CHAT,
    ]);
    expect(model.messages).toBe(1);
    expect(model.edges[0]).toMatchObject({
      source: "alpha/one",
      target: "beta/two",
      delivered: 1,
      health: "healthy",
    });
    expect(JSON.stringify(model)).not.toContain("secret body");
    expect(model.projects.map((project) => project.id)).toEqual(["alpha", "beta"]);
  });

  it("uses the strongest final receipt outcome once and honours the brushed window", () => {
    const model = deriveCommunicationModel(
      [
        event(23, "delivery_receipt_expired", {
          message_seq: 20,
          expired: true,
        }),
        event(22, "delivery_receipt_deferred", {
          message_seq: 20,
          deferred: true,
          delivered: true,
        }),
        event(
          20,
          "chat",
          {
            sender: "alpha/one",
            target: "beta/two",
            type: "chat",
            payload: "x",
          },
          20,
        ),
        CHAT,
      ],
      [],
      ["quiet/agent"],
      { fromTs: 15, toTs: 30 },
    );
    expect(model.messages).toBe(1);
    expect(model.edges[0]).toMatchObject({
      failed: 1,
      deferred: 0,
      health: "failed",
    });
    expect(model.nodes.some((node) => node.id === "quiet/agent" && node.messages === 0)).toBe(true);
  });

  it("bounds the matrix and lays out identical data identically", () => {
    const events = Array.from({ length: 15 }, (_, index) =>
      event(index + 1, "chat", {
        sender: `p/a${index}`,
        target: "q/sink",
        type: "chat",
        payload: "x",
      }),
    );
    const model = deriveCommunicationModel(events);
    expect(matrixIdentities(model)).toHaveLength(12);
    expect(layoutCommunicationWeb(model).nodes).toHaveLength(16);
    expect(layoutCommunicationWeb(model, 760, 360, 8).nodes).toHaveLength(8);
    expect(layoutCommunicationWeb(model)).toEqual(layoutCommunicationWeb(model));
    expect(projectOf("bare")).toBe("unscoped");
    expect(projectOf("SYNAPSE-CHANNEL")).toBe("SYNAPSE-CHANNEL");
    expect(projectOf("CEO")).toBe("fleet-wide");
  });

  it("reveals bodies only in a selected pair timeline and correlates semantic responses", () => {
    const response = event(15, "ack", {
      sender: "beta/two",
      target: "alpha/one",
      type: "chat",
      payload: "Acknowledged.",
      response_to_seq: 10,
      response_status: "acknowledged",
      response_evidence_scope: "recipient",
    });
    const receipt = event(16, "delivery_receipt_immediate", {
      message_seq: 15,
      delivered: true,
    });
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
    expect(detail[1]).toMatchObject({
      seq: 10,
      body: "secret body",
      delivery: "unknown",
    });
    expect(deriveConversationDetail([CHAT], "alpha/one", "other/three")).toEqual([]);
  });
});
