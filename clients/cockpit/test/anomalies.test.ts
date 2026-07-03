// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — repetition-heuristic tests

import { describe, expect, it } from "vitest";
import { deriveAnomalies } from "../src/lib/anomalies";
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

describe("deriveAnomalies", () => {
  it("flags a task claimed three or more times, with the observed count", () => {
    const flags = deriveAnomalies([
      event(5, { taskId: "hot" }),
      event(4, { taskId: "hot" }),
      event(3, { taskId: "hot" }),
      event(2, { taskId: "calm" }),
      event(1, { taskId: "calm" }),
    ]);
    expect(flags).toHaveLength(1);
    expect(flags[0]).toMatchObject({
      taskId: "hot",
      kind: "claim_churn",
      count: 3,
      lastTs: 5,
      detail: "claimed 3x in the observed window",
    });
  });

  it("flags repeated lease expiries separately and orders by newest evidence", () => {
    const flags = deriveAnomalies([
      event(9, { taskId: "leaky", kind: "lease" }),
      event(8, { taskId: "churny" }),
      event(7, { taskId: "churny" }),
      event(6, { taskId: "churny" }),
      event(5, { taskId: "leaky", kind: "lease" }),
    ]);
    expect(flags.map((flag) => [flag.taskId, flag.kind])).toEqual([
      ["leaky", "lease_repeat"],
      ["churny", "claim_churn"],
    ]);
    expect(flags[0]?.detail).toBe("lease expired 2x in the observed window");
  });

  it("ignores taskless events, non-claim kinds, and below-threshold repetition", () => {
    expect(
      deriveAnomalies([
        event(4, { taskId: "" }),
        event(3, { kind: "release" }),
        event(2, { kind: "chat", taskId: "t" }),
        event(1, { taskId: "once", kind: "lease" }),
      ]),
    ).toEqual([]);
    expect(deriveAnomalies([])).toEqual([]);
  });

  it("keeps the newest timestamp even when events arrive out of order", () => {
    const flags = deriveAnomalies([
      event(1, { taskId: "hot", ts: 10 }),
      event(2, { taskId: "hot", ts: 30 }),
      event(3, { taskId: "hot", ts: 20 }),
    ]);
    expect(flags[0]?.lastTs).toBe(30);
  });

  it("breaks equal-time ties by task id", () => {
    const flags = deriveAnomalies([
      event(1, { taskId: "beta", ts: 10 }),
      event(2, { taskId: "beta", ts: 10 }),
      event(3, { taskId: "beta", ts: 10 }),
      event(4, { taskId: "alfa", ts: 10 }),
      event(5, { taskId: "alfa", ts: 10 }),
      event(6, { taskId: "alfa", ts: 10 }),
    ]);
    expect(flags.map((flag) => flag.taskId)).toEqual(["alfa", "beta"]);
  });
});
