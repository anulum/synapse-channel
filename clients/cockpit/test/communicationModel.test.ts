// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — fleet communication projection tests

import { describe, expect, it } from "vitest";

import { deriveCommunicationModel, projectOf } from "../src/lib/communicationModel";
import type { ClaimView } from "../src/lib/claims";
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

function claim(owner: string, taskId: string): ClaimView {
  return {
    claim: {
      task_id: taskId,
      owner,
      lease_expires_at: null,
      paths: [],
      stale: false,
      git: null,
    },
    urgency: "held",
    inConflict: false,
    secondsToExpiry: null,
  };
}

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
        event(23, "delivery_receipt_expired", { message_seq: 20, expired: true }),
        event(22, "delivery_receipt_deferred", { message_seq: 20, deferred: true, delivered: true }),
        event(20, "chat", { sender: "alpha/one", target: "beta/two", type: "chat", payload: "x" }, 20),
        CHAT,
      ],
      [],
      ["quiet/agent"],
      { fromTs: 15, toTs: 30 },
    );
    expect(model.messages).toBe(1);
    expect(model.edges[0]).toMatchObject({ failed: 1, deferred: 0, health: "failed" });
    expect(model.nodes.some((node) => node.id === "quiet/agent" && node.messages === 0)).toBe(true);
  });

  it("keeps malformed evidence inert and covers every receipt health outcome", () => {
    const noPayload: CockpitEvent = {
      seq: 1,
      ts: 1,
      kind: "chat",
      lane: "task",
      severity: 0.2,
      actor: "",
      label: "no payload",
      taskId: "",
    };
    let payloadReads = 0;
    const changingPayload = {
      seq: 2,
      ts: 2,
      kind: "chat",
      lane: "task",
      severity: 0.2,
      actor: "",
      label: "changing payload",
      taskId: "",
      get payload() {
        payloadReads += 1;
        return payloadReads === 1
          ? { sender: "volatile/a", target: "volatile/b", type: "chat" }
          : { sender: "", target: "volatile/b", type: "chat" };
      },
    } satisfies CockpitEvent;
    const model = deriveCommunicationModel(
      [
        noPayload,
        changingPayload,
        event(3, "not a chat", { sender: 7, target: "q/b", type: "chat" }),
        event(40, "delivery_receipt_immediate", { message_seq: 30, delivered: false }),
        event(41, "delivery_receipt_other", { message_seq: 31, deferred: true }),
        event(42, "delivery_receipt_other", { message_seq: 999, expired: true }),
        event(43, "delivery_receipt_unknown", { message_seq: "bad" }),
        event(44, "delivery_receipt_other", { message_seq: 34, deferred: true }),
        event(45, "delivery_receipt_immediate", { message_seq: 34, delivered: true }),
        event(30, "chat", { sender: "p/a", target: "q/b", type: "chat", payload: "a" }, 10),
        event(31, "chat", { sender: "p/c", target: "q/d", type: "chat", payload: "b" }, 10),
        event(32, "chat", { sender: "p/e", target: "q/f", type: "chat", payload: "c" }, 10),
        event(33, "chat", { sender: "p/e", target: "q/f", type: "chat", payload: "d" }, 10),
        event(34, "chat", { sender: "p/g", target: "q/h", type: "chat", payload: "e" }, 10),
      ],
      [claim("p/owner", "T-1"), claim("p/owner", "T-2"), claim("", "T-3")],
      ["", " quiet/agent ", "wild/*", "wild/?", "bare"],
    );
    expect(model.edges.find((edge) => edge.source === "p/a")?.health).toBe("failed");
    expect(model.edges.find((edge) => edge.source === "p/c")?.health).toBe("deferred");
    expect(model.edges.find((edge) => edge.source === "p/e")?.health).toBe("unknown");
    expect(model.edges.find((edge) => edge.source === "p/g")?.health).toBe("deferred");
    expect(model.nodes.find((node) => node.id === "wild/*")?.exact).toBe(false);
    expect(model.nodes.find((node) => node.id === "wild/?")?.exact).toBe(false);
    expect(model.projects.find((project) => project.id === "p")?.claims).toBe(2);
  });

  it("derives scoped, fleet-wide, canonical, and unscoped project names", () => {
    expect(projectOf("alpha/one")).toBe("alpha");
    expect(projectOf("all")).toBe("fleet-wide");
    expect(projectOf("CEO")).toBe("fleet-wide");
    expect(projectOf("SYNAPSE-CHANNEL")).toBe("SYNAPSE-CHANNEL");
    expect(projectOf("bare")).toBe("unscoped");
  });
});
