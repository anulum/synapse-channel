// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — snapshot-transition event derivation tests

import { describe, expect, it } from "vitest";
import type { SnapshotState, SnapshotStore } from "../src/lib/snapshot";
import { createSnapshotEventSource, deriveTransitionEvents } from "../src/lib/spineEvents";
import type { ClaimRecord, CockpitEvent, FleetSnapshot, RiskSignal } from "../src/types";

function claim(owner: string, taskId: string, paths: readonly string[] = []): ClaimRecord {
  return { task_id: taskId, owner, lease_expires_at: null, paths, stale: false, git: null };
}

interface SnapshotOverrides {
  readonly live?: readonly string[];
  readonly active?: readonly ClaimRecord[];
  readonly stale?: readonly ClaimRecord[];
  readonly board?: Record<string, unknown>;
  readonly signals?: readonly RiskSignal[];
}

function snapshotOf(overrides: SnapshotOverrides = {}): FleetSnapshot {
  const active = overrides.active ?? [];
  const stale = overrides.stale ?? [];
  return {
    hub_version: "",
    config_epoch: "",
    online_agents: [...(overrides.live ?? [])],
  agent_roles: {},
    state: {},
    board: overrides.board ?? {},
    manifest: [],
    fleet: {
      agents: { live: [...(overrides.live ?? [])], waiters: [], missing_waiters: [] },
      claims: {
        active: active.length,
        stale: stale.length,
        active_claims: active,
        stale_claims: stale,
      },
      branch_conflicts: [],
      task_graph: { nodes: [], edges: [] },
      receipts: [],
    },
    risk: {
      level: "green",
      signals: [...(overrides.signals ?? [])],
      safe_next_work: [],
    },
  };
}

describe("deriveTransitionEvents", () => {
  it("yields nothing for identical snapshots", () => {
    const snapshot = snapshotOf({ live: ["a"], active: [claim("a", "t1")] });
    expect(deriveTransitionEvents(snapshot, snapshot, 5_000, 1)).toEqual([]);
  });

  it("derives roster joins and leaves as presence events", () => {
    const events = deriveTransitionEvents(
      snapshotOf({ live: ["stays", "leaves"] }),
      snapshotOf({ live: ["stays", "joins"] }),
      12_000,
      1,
    );
    expect(events.map((event) => [event.kind, event.actor, event.label])).toEqual([
      ["presence", "joins", "joined the roster"],
      ["presence", "leaves", "left the roster"],
    ]);
    for (const event of events) {
      expect(event.lane).toBe("presence");
      expect(event.ts).toBe(12);
    }
  });

  it("derives the claim lifecycle: claimed, lease expired, released, stale cleared", () => {
    const previous = snapshotOf({
      active: [claim("a", "goes-stale"), claim("a", "gets-released")],
      stale: [claim("b", "gets-cleared")],
    });
    const next = snapshotOf({
      active: [claim("a", "brand-new")],
      stale: [claim("a", "goes-stale")],
    });
    const events = deriveTransitionEvents(previous, next, 8_000, 1);
    expect(events.map((event) => [event.kind, event.actor, event.label])).toEqual([
      ["claim", "a", "claimed brand-new"],
      ["lease", "a", "lease expired on goes-stale"],
      ["release", "a", "released gets-released"],
      ["release", "b", "stale claim gets-cleared cleared"],
    ]);
    // Every claim-lane transition names its task, so the log can hop into the
    // causality inspector for it.
    expect(events.map((event) => event.taskId)).toEqual([
      "brand-new",
      "goes-stale",
      "gets-released",
      "gets-cleared",
    ]);
  });

  it("does not re-announce a claim resurfacing from the stale bucket", () => {
    const previous = snapshotOf({ stale: [claim("a", "t1")] });
    const next = snapshotOf({ active: [claim("a", "t1")] });
    expect(deriveTransitionEvents(previous, next, 1_000, 1)).toEqual([]);
  });

  it("stays silent while a claim persists in the same bucket", () => {
    const previous = snapshotOf({ active: [claim("a", "t1")], stale: [claim("b", "t2")] });
    const next = snapshotOf({ active: [claim("a", "t1")], stale: [claim("b", "t2")] });
    expect(deriveTransitionEvents(previous, next, 1_000, 1)).toEqual([]);
  });

  it("derives board task declarations and status transitions", () => {
    const previous = snapshotOf({
      board: {
        tasks: [
          { task_id: "t1", status: "open" },
          { task_id: "t2", status: "open" },
          { task_id: "", status: "open" },
          "not-a-record",
        ],
      },
    });
    const next = snapshotOf({
      board: {
        tasks: [
          { task_id: "t1", status: "done" },
          { task_id: "t2", status: "open" },
          { task_id: "t3", status: "open" },
        ],
      },
    });
    const events = deriveTransitionEvents(previous, next, 3_000, 1);
    expect(events.map((event) => event.label)).toEqual([
      "task t1: open → done",
      "task t3 declared (open)",
    ]);
    for (const event of events) expect(event.kind).toBe("task");
  });

  it("derives new progress notes, honouring the finding kind and bare notes", () => {
    const previous = snapshotOf({ board: { progress: [{ author: "old", kind: "note", text: "seen" }] } });
    const next = snapshotOf({
      board: {
        progress: [
          { author: "old", kind: "note", text: "seen" },
          { author: "a", kind: "finding", task_id: "t1", text: "found it" },
          { author: "b", kind: "note", text: "just a note" },
        ],
      },
    });
    const events = deriveTransitionEvents(previous, next, 9_000, 1);
    expect(events.map((event) => [event.kind, event.actor, event.label])).toEqual([
      ["finding", "a", "t1: found it"],
      ["chat", "b", "just a note"],
    ]);
    expect(events.map((event) => event.taskId)).toEqual(["t1", ""]);
  });

  it("tolerates a missing or non-list progress payload", () => {
    const previous = snapshotOf({ board: {} });
    const next = snapshotOf({ board: { progress: "corrupt" } });
    expect(deriveTransitionEvents(previous, next, 1_000, 1)).toEqual([]);
  });

  it("raises a conflict impulse only for newly red risk signals", () => {
    const persisting: RiskSignal = {
      level: "red",
      category: "stale_claim",
      subject: "old",
      detail: "",
    };
    const previous = snapshotOf({ signals: [persisting] });
    const next = snapshotOf({
      signals: [
        persisting,
        { level: "red", category: "conflict", subject: "clients/cockpit", detail: "two owners" },
        { level: "amber", category: "lease", subject: "soon", detail: "" },
      ],
    });
    const events = deriveTransitionEvents(previous, next, 7_000, 1);
    expect(events).toHaveLength(1);
    const event = events[0] as CockpitEvent;
    expect(event.kind).toBe("conflict");
    expect(event.lane).toBe("risk");
    expect(event.label).toBe("conflict: clients/cockpit");
    expect(event.severity).toBeGreaterThan(0.8);
  });

  it("numbers events from seqStart in a stable section order", () => {
    const previous = snapshotOf({ live: [] });
    const next = snapshotOf({
      live: ["newcomer"],
      active: [claim("newcomer", "t1")],
      signals: [{ level: "red", category: "conflict", subject: "s", detail: "" }],
    });
    const events = deriveTransitionEvents(previous, next, 4_000, 10);
    expect(events.map((event) => [event.seq, event.kind])).toEqual([
      [10, "presence"],
      [11, "claim"],
      [12, "conflict"],
    ]);
  });
});

/** A hand-driven snapshot store: tests publish states, the source reacts. */
function scriptedStore(): {
  store: SnapshotStore;
  publish: (state: SnapshotState) => void;
  detached: () => boolean;
} {
  const listeners = new Set<(state: SnapshotState) => void>();
  let detached = false;
  return {
    store: {
      subscribe(listener) {
        listeners.add(listener);
        return () => {
          listeners.delete(listener);
          detached = true;
        };
      },
      stop() {
        listeners.clear();
      },
    },
    publish(state) {
      for (const listener of listeners) listener(state);
    },
    detached: () => detached,
  };
}

function liveState(snapshot: FleetSnapshot, fetchedAt: number): SnapshotState {
  return { snapshot, status: "live", fetchedAt, error: null };
}

describe("createSnapshotEventSource", () => {
  it("absorbs the first fetch silently, then emits diffs with continuous seq", () => {
    const { store, publish } = scriptedStore();
    const source = createSnapshotEventSource(store);
    const seen: CockpitEvent[] = [];
    source.subscribe((event) => seen.push(event));

    publish(liveState(snapshotOf({ live: ["a"] }), 1_000));
    expect(seen).toEqual([]);

    publish(liveState(snapshotOf({ live: ["a", "b"] }), 3_000));
    publish(liveState(snapshotOf({ live: ["a", "b", "c"] }), 5_000));

    expect(seen.map((event) => [event.seq, event.actor])).toEqual([
      [1, "b"],
      [2, "c"],
    ]);
    source.stop();
  });

  it("ignores states with no snapshot, no fetch stamp, or an unchanged fetch stamp", () => {
    const { store, publish } = scriptedStore();
    const source = createSnapshotEventSource(store);
    const seen: CockpitEvent[] = [];
    source.subscribe((event) => seen.push(event));

    publish({ snapshot: null, status: "connecting", fetchedAt: null, error: null });
    publish({ snapshot: snapshotOf({ live: ["a"] }), status: "error", fetchedAt: null, error: "down" });
    publish(liveState(snapshotOf({ live: ["a"] }), 1_000));
    // A freshness re-evaluation republishes the same fetch: it must not diff.
    publish(liveState(snapshotOf({ live: ["a", "ghost"] }), 1_000));
    publish(liveState(snapshotOf({ live: ["a"] }), 2_000));

    expect(seen).toEqual([]);
    source.stop();
  });

  it("stops delivering to an unsubscribed listener while others continue", () => {
    const { store, publish } = scriptedStore();
    const source = createSnapshotEventSource(store);
    const first: CockpitEvent[] = [];
    const second: CockpitEvent[] = [];
    const unsubscribeFirst = source.subscribe((event) => first.push(event));
    source.subscribe((event) => second.push(event));

    publish(liveState(snapshotOf({ live: [] }), 1_000));
    publish(liveState(snapshotOf({ live: ["a"] }), 2_000));
    unsubscribeFirst();
    publish(liveState(snapshotOf({ live: ["a", "b"] }), 3_000));

    expect(first).toHaveLength(1);
    expect(second).toHaveLength(2);
    source.stop();
  });

  it("detaches from the store on stop", () => {
    const { store, publish, detached } = scriptedStore();
    const source = createSnapshotEventSource(store);
    const seen: CockpitEvent[] = [];
    source.subscribe((event) => seen.push(event));

    publish(liveState(snapshotOf({ live: [] }), 1_000));
    source.stop();
    expect(detached()).toBe(true);

    publish(liveState(snapshotOf({ live: ["late"] }), 2_000));
    expect(seen).toEqual([]);
  });
});
