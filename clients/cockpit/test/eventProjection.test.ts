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

  it("keeps a task update on the claims lane with its owner and status", () => {
    // The hub journals the updated claim itself (`claim.as_persisted_dict()`).
    const update = stored(10, "task_update", {
      task_id: "HARBOUR-104",
      owner: "harbour/web",
      status: "working",
      paths: ["src/web/audit"],
    });
    expect(mapStoredEvent(update)).toMatchObject({
      kind: "claim",
      lane: "claims",
      actor: "harbour/web",
      taskId: "HARBOUR-104",
      label: "updated HARBOUR-104 (working)",
    });
    expect(mapStoredEvent(stored(11, "task_update", { task_id: "t4" }))).toMatchObject({
      actor: "",
      label: "updated t4",
    });
  });

  it("shows a recorded finding with the author the hub attested", () => {
    const finding = stored(12, "finding", {
      statement: "Upload limits are enforced by the API, not by the web client.",
      subkind: "decision",
      provenance: { actor: "harbour/reviewer", project: "harbour" },
    });
    expect(mapStoredEvent(finding)).toMatchObject({
      kind: "finding",
      lane: "task",
      actor: "harbour/reviewer",
      label: "Upload limits are enforced by the API, not by the web client.",
    });
    const long = mapStoredEvent(stored(13, "finding", { statement: "y".repeat(400) }));
    expect(long.actor).toBe("");
    expect(long.label.endsWith("…")).toBe(true);
    expect(mapStoredEvent(stored(14, "finding", { provenance: "not a record" })).actor).toBe("");
    expect(mapStoredEvent(stored(15, "finding", { provenance: null })).actor).toBe("");
  });

  it("puts an operator relay on the task lane and a refused one on the risk lane", () => {
    const relay = (seq: number, payload: Record<string, unknown>) =>
      mapStoredEvent(
        stored(seq, "operator_relay", {
          action: "release",
          task_id: "SEED-1",
          operator: "operator:seed",
          ...payload,
        }),
      );
    // Recorded statuses: "applied" and "pending" (`core/handlers/operator_relay.py`
    // RELAY_STATUS_*), "not_applied" (an approved quorum that found no live lease).
    expect(relay(20, { status: "applied", applied: true })).toMatchObject({
      kind: "task",
      lane: "task",
      actor: "operator:seed",
      label: "operator release SEED-1 (applied)",
    });
    expect(relay(21, { status: "pending", applied: false }).lane).toBe("task");
    expect(relay(22, { status: "not_applied", applied: false }).lane).toBe("task");
    // The outbound half records no status; the dashboard's operator-actions feed
    // (`dashboard_store_feeds._operator_action_row`) reads it as applied, pending or
    // refused, and so does the signal log. Only "refused" is a risk.
    expect(relay(23, { applied: true }).label).toBe("operator release SEED-1 (applied)");
    expect(relay(24, { pending: true }).lane).toBe("task");
    expect(relay(25, { applied: false, detail: "relay to the owning hub failed" })).toMatchObject({
      kind: "conflict",
      lane: "risk",
      label: "operator release SEED-1 (refused)",
    });
  });

  it("takes the hub's words for actor and line, and keeps its own lane", () => {
    const relay = mapStoredEvent({
      ...stored(40, "operator_relay", { operator: "cockpit-own", action: "release", applied: false }),
      words: { actor: "op/hub", summary: "operator release T-1 (refused)" },
    });
    expect([relay.kind, relay.lane, relay.actor, relay.label]).toEqual([
      "conflict",
      "risk",
      "op/hub",
      "operator release T-1 (refused)",
    ]);
    const unknown = mapStoredEvent({
      ...stored(41, "mailbox_watermark", { identity: "P/a" }),
      words: { actor: "P/a", summary: "mailbox read through 4" },
    });
    expect([unknown.kind, unknown.lane, unknown.actor, unknown.label]).toEqual([
      "chat",
      "presence",
      "P/a",
      "mailbox read through 4",
    ]);
    const silent = mapStoredEvent({ ...stored(42, "release", { task_id: "T-2" }), words: { actor: "", summary: "released T-2" } });
    expect([silent.actor, silent.label, silent.storedKind]).toEqual(["", "released T-2", "release"]);
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
