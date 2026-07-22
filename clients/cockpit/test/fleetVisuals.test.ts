// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — temporal-lane and project-flow evidence tests

import { describe, expect, it } from "vitest";

import type { ClaimView } from "../src/lib/claims";
import { deriveFleetTimeline, deriveProjectFlow, layoutProjectFlow } from "../src/lib/fleetVisuals";
import type { CockpitEvent, EventKind, Lane } from "../src/types";

function event(
  seq: number,
  kind: EventKind,
  label: string,
  actor = "alpha/one",
  payload?: Record<string, unknown>,
): CockpitEvent {
  const lane: Lane =
    kind === "presence" ? "presence" : kind === "claim" || kind === "lease" || kind === "release" ? "claims" : "task";
  return {
    seq,
    ts: seq * 10,
    kind,
    lane,
    severity: 0.3,
    actor,
    label,
    taskId: kind === "task" ? `TASK-${seq}` : "",
    ...(payload === undefined ? {} : { payload }),
  };
}

function chat(seq: number, source: string, target: string, withType = true): CockpitEvent {
  return event(seq, "chat", `message ${seq}`, source, {
    sender: source,
    target,
    ...(withType ? { type: "chat" } : { payload: "body is not projected" }),
  });
}

function claim(owner: string, taskId: string, inConflict = false): ClaimView {
  return {
    claim: {
      task_id: taskId,
      owner,
      lease_expires_at: null,
      paths: [],
      stale: false,
      git: null,
    },
    urgency: inConflict ? "conflict" : "held",
    inConflict,
    secondsToExpiry: null,
  };
}

describe("deriveFleetTimeline", () => {
  it("maps every supported retained event family and leaves unrelated presence inert", () => {
    const result = deriveFleetTimeline([
      event(1, "presence", "connected"),
      event(2, "presence", "receiver online", "alpha/one-rx"),
      event(3, "presence", "waiting for work", "beta/two"),
      event(4, "claim", "claim"),
      event(5, "lease", "lease"),
      event(6, "release", "release"),
      event(7, "conflict", "conflict"),
      event(8, "task", "task"),
      event(9, "finding", "finding"),
      chat(10, "gamma/three", "delta/four"),
      event(11, "presence", "delivery_receipt_immediate", "", {
        message_seq: 10,
      }),
    ]);
    expect(result.points.map((point) => point.lane)).toEqual([
      "wait",
      "wait",
      "claim",
      "claim",
      "claim",
      "claim",
      "task",
      "task",
      "message",
      "message",
    ]);
    expect(result.points.find((point) => point.seq === 10)).toMatchObject({
      actor: "gamma/three",
      project: "gamma",
    });
    expect(result.firstTs).toBe(20);
    expect(result.lastTs).toBe(110);
    expect(result.limited).toBe(false);
  });

  it("bounds to newest evidence, honours the brushed window, and positions a single event", () => {
    const events = [event(1, "task", "old"), event(2, "task", "middle"), event(3, "task", "new")];
    const bounded = deriveFleetTimeline(events, null, 2);
    expect(bounded.points.map((point) => point.seq)).toEqual([2, 3]);
    expect(bounded.points.map((point) => point.position)).toEqual([0, 1]);
    expect(bounded).toMatchObject({ total: 3, limited: true });
    expect(deriveFleetTimeline(events, { fromTs: 20, toTs: 20 }, 0).points[0]).toMatchObject({
      seq: 2,
      position: 0.5,
    });
    expect(deriveFleetTimeline([], null)).toEqual({
      points: [],
      firstTs: null,
      lastTs: null,
      total: 0,
      limited: false,
    });
    const tied = [{ ...event(2, "task", "second"), ts: 10 }, event(1, "task", "first")];
    expect(deriveFleetTimeline(tied).points.map((point) => point.seq)).toEqual([1, 2]);
  });
});

describe("deriveProjectFlow", () => {
  it("aggregates exact message sequences, ownership, and contention without bodies", () => {
    const model = deriveProjectFlow(
      [
        chat(1, "alpha/one", "beta/two"),
        chat(2, "alpha/three", "beta/two", false),
        chat(3, "beta/two", "alpha/one"),
        event(4, "chat", "missing target", "alpha/one", {
          sender: "alpha/one",
          type: "chat",
        }),
        event(5, "chat", "not chat evidence", "alpha/one", {
          sender: "alpha/one",
          target: "beta/two",
          type: "notice",
        }),
      ],
      [claim("alpha/owner", "A", true), claim("alpha/owner", "B"), claim("", "ignored")],
    );
    expect(model.messages).toBe(3);
    expect(model.links).toEqual([
      expect.objectContaining({
        source: "alpha",
        target: "beta",
        messages: 2,
        evidenceSeqs: [2, 1],
      }),
      expect.objectContaining({
        source: "beta",
        target: "alpha",
        messages: 1,
        evidenceSeqs: [3],
      }),
    ]);
    expect(model.projects.find((project) => project.id === "alpha")).toMatchObject({
      members: ["alpha/one", "alpha/owner", "alpha/three"],
      inbound: 1,
      outbound: 2,
      claims: 2,
      conflicts: 1,
      lastTs: 30,
    });
    expect(JSON.stringify(model)).not.toContain("body is not projected");
    expect(model.limited).toBe(false);
  });

  it("honours the retained window and reports project/link bounds honestly", () => {
    const events = [chat(1, "a/one", "b/two"), chat(2, "c/one", "d/two"), chat(3, "e/one", "f/two")];
    const bounded = deriveProjectFlow(events, [], null, 4, 1);
    expect(bounded.projects).toHaveLength(4);
    expect(bounded.links).toHaveLength(1);
    expect(bounded.limited).toBe(true);
    expect(deriveProjectFlow(events, [], { fromTs: 20, toTs: 20 }).messages).toBe(1);
    expect(deriveProjectFlow([], [claim("quiet/owner", "Q")])).toMatchObject({
      messages: 0,
      links: [],
      limited: false,
    });
    const sameTime = [chat(1, "a/one", "c/one"), { ...chat(2, "b/one", "d/one"), ts: 10 }];
    expect(deriveProjectFlow(sameTime).links.map((link) => link.id)).toEqual(["a\u0000c", "b\u0000d"]);
  });
});

describe("layoutProjectFlow", () => {
  it("places ranked sources and targets deterministically at responsive anchors", () => {
    const model = deriveProjectFlow([
      chat(1, "alpha/one", "beta/two"),
      chat(2, "alpha/one", "gamma/three"),
      chat(3, "gamma/three", "beta/two"),
    ]);
    const layout = layoutProjectFlow(model, 500, 200);
    expect([...layout.sources.keys()]).toEqual(["alpha", "gamma"]);
    expect([...layout.targets.keys()]).toEqual(["beta", "gamma"]);
    expect(layout.sources.get("alpha")).toMatchObject({
      id: "alpha",
      x: 90,
    });
    expect(layout.sources.get("alpha")?.y).toBeCloseTo(200 / 3);
    expect(layout.targets.get("beta")).toMatchObject({
      id: "beta",
      x: 410,
    });
    expect(layout.targets.get("beta")?.y).toBeCloseTo(200 / 3);
    expect(layoutProjectFlow(deriveProjectFlow([]))).toEqual({
      sources: new Map(),
      targets: new Map(),
    });
    const tied = layoutProjectFlow(deriveProjectFlow([chat(1, "a/one", "c/one"), chat(2, "b/one", "d/one")]));
    expect([...tied.sources.keys()]).toEqual(["a", "b"]);
    expect([...tied.targets.keys()]).toEqual(["c", "d"]);
  });
});
