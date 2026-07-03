// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — compact log grouping tests

import { describe, expect, it } from "vitest";
import { groupByTask } from "../src/lib/logGroups";
import type { CockpitEvent } from "../src/types";

function event(seq: number, overrides: Partial<CockpitEvent> = {}): CockpitEvent {
  return {
    seq,
    ts: seq,
    kind: "claim",
    lane: "claims",
    severity: 0.5,
    actor: "a",
    label: "claimed t",
    taskId: "t",
    ...overrides,
  };
}

describe("groupByTask", () => {
  it("groups a lifecycle oldest-first inside newest-activity-first groups", () => {
    // Newest-first input: t2's release (5), t1's release (4), t2's claim (3), t1's claim (2).
    const events = [
      event(5, { taskId: "t2", kind: "release", actor: "" }),
      event(4, { taskId: "t1", kind: "release", actor: "b" }),
      event(3, { taskId: "t2", kind: "claim", actor: "c" }),
      event(2, { taskId: "t1", kind: "claim", actor: "b" }),
    ];
    const compact = groupByTask(events);
    expect(compact.groups.map((group) => group.taskId)).toEqual(["t2", "t1"]);
    expect(compact.groups[0]?.events.map((item) => item.seq)).toEqual([3, 5]);
    expect(compact.groups[0]?.lastTs).toBe(5);
    // The newest NAMED actor wins, skipping actor-less events.
    expect(compact.groups[0]?.lastActor).toBe("c");
    expect(compact.groups[1]?.lastActor).toBe("b");
    expect(compact.ungrouped).toEqual([]);
  });

  it("keeps taskless events flat and never mutates the input", () => {
    const events = [
      event(3, { taskId: "", kind: "chat", label: "hello" }),
      event(2),
      event(1, { taskId: "", kind: "presence", actor: "" }),
    ];
    const before = [...events];
    const compact = groupByTask(events);
    expect(compact.ungrouped.map((item) => item.seq)).toEqual([3, 1]);
    expect(compact.groups).toHaveLength(1);
    expect(events).toEqual(before);
  });

  it("orders equal-time groups by task id and handles the empty log", () => {
    const events = [event(2, { taskId: "beta", ts: 10 }), event(1, { taskId: "alfa", ts: 10 })];
    const compact = groupByTask(events);
    expect(compact.groups.map((group) => group.taskId)).toEqual(["alfa", "beta"]);
    expect(groupByTask([])).toEqual({ groups: [], ungrouped: [] });
  });

  it("falls back to an empty actor when no event in the group names one", () => {
    const compact = groupByTask([event(1, { actor: "" })]);
    expect(compact.groups[0]?.lastActor).toBe("");
  });
});
