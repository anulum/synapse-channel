// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — durable-event semantic projection contracts

import { describe, expect, it } from "vitest";

import { mapStoredEvent } from "../src/lib/eventProjection";
import type { StoredEvent } from "../src/lib/eventTailParser";

function stored(seq: number, kind: string, payload: Record<string, unknown>): StoredEvent {
  return { seq, ts: seq * 10, kind, payload };
}

describe("durable-event projection", () => {
  it("maps a dead-letter escalation into the risk lane, loud", () => {
    const event = mapStoredEvent({
      seq: 9001,
      ts: 1783.5,
      kind: "dead_letter_escalation",
      payload: { target: "CEO", count: 10, last_sender: "a/say", threshold: 5 },
    });
    expect(event.kind).toBe("conflict");
    expect(event.lane).toBe("risk");
    expect(event.actor).toBe("CEO");
    expect(event.label).toBe("dead-letter escalation: CEO · 10 undelivered");
    const bare = mapStoredEvent({
      seq: 1,
      ts: 1,
      kind: "dead_letter_escalation",
      payload: { target: "x" },
    });
    expect(bare.label).toBe("dead-letter escalation: x");
  });

  it("maps a dead-letter forward as an audit finding, direction and hubs named", () => {
    const event = mapStoredEvent({
      seq: 9002,
      ts: 1784.5,
      kind: "dead_letter_forwarding",
      payload: {
        target: "CEO",
        count: 10,
        origin_hub_id: "hub-a",
        owner_hub_id: "hub-b",
        direction: "out",
      },
    });
    expect(event.kind).toBe("finding");
    expect(event.actor).toBe("CEO");
    expect(event.label).toBe("dead-letter forward (out): CEO · 10 undelivered · hub-a → hub-b");
    const bare = mapStoredEvent({
      seq: 1,
      ts: 1,
      kind: "dead_letter_forwarding",
      payload: { target: "x" },
    });
    expect(bare.label).toBe("dead-letter forward: x");
    const half = mapStoredEvent({
      seq: 2,
      ts: 2,
      kind: "dead_letter_forwarding",
      payload: { target: "y", count: "many", origin_hub_id: "hub-a", direction: "in" },
    });
    expect(half.label).toBe("dead-letter forward (in): y");
  });

  it("maps every known hub kind with the hub's own seq and ts", () => {
    expect(mapStoredEvent(stored(1, "claim", { task_id: "t1", owner: "a" }))).toMatchObject({
      seq: 1,
      ts: 10,
      kind: "claim",
      lane: "claims",
      actor: "a",
      taskId: "t1",
      label: "claimed t1",
    });
    expect(mapStoredEvent(stored(2, "release", { task_id: "t1" }))).toMatchObject({
      kind: "release",
      actor: "",
      label: "released t1",
    });
    expect(
      mapStoredEvent(
        stored(3, "ledger_progress", {
          task_id: "t1",
          author: "b",
          kind: "finding",
          text: "found",
        }),
      ),
    ).toMatchObject({ kind: "finding", actor: "b", label: "t1: found" });
    expect(
      mapStoredEvent(
        stored(4, "ledger_progress", { author: "b", kind: "note", text: "bare note" }),
      ),
    ).toMatchObject({ kind: "chat", label: "bare note", taskId: "" });
    expect(
      mapStoredEvent(
        stored(5, "ledger_task", { task_id: "t2", status: "open", created_by: "c" }),
      ),
    ).toMatchObject({ kind: "task", actor: "c", label: "task t2 (open)" });
    expect(mapStoredEvent(stored(6, "ledger_task", { task_id: "t3" }))).toMatchObject({
      label: "task t3",
    });
    expect(
      mapStoredEvent(stored(7, "chat", { sender: "d", payload: "hello fleet" })),
    ).toMatchObject({ kind: "chat", actor: "d", label: "hello fleet" });
  });

  it("truncates an over-long chat payload and shows unknown kinds by name", () => {
    const long = "x".repeat(400);
    const chat = mapStoredEvent(stored(8, "chat", { sender: "d", payload: long }));
    expect(chat.label.length).toBeLessThan(200);
    expect(chat.label.endsWith("…")).toBe(true);
    expect(mapStoredEvent(stored(9, "checkpoint", {}))).toMatchObject({
      kind: "chat",
      label: "checkpoint",
      actor: "",
    });
  });
});
