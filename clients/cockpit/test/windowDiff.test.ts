// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — window-diff arithmetic tests

import { describe, expect, it } from "vitest";
import { diffWindows, observedRate } from "../src/lib/windowDiff";
import type { CockpitEvent } from "../src/types";

function event(seq: number, overrides: Partial<CockpitEvent> = {}): CockpitEvent {
  return {
    seq,
    ts: seq,
    kind: "claim",
    lane: "claims",
    severity: 0.5,
    actor: "a",
    label: "x",
    taskId: "",
    ...overrides,
  };
}

describe("observedRate", () => {
  it("measures events per minute over the window's own span", () => {
    // 3 events over 60 seconds → 3/min.
    expect(observedRate([event(1, { ts: 0 }), event(2, { ts: 30 }), event(3, { ts: 60 })])).toBe(3);
  });

  it("refuses to invent a duration", () => {
    expect(observedRate([])).toBeNull();
    expect(observedRate([event(1)])).toBeNull();
    // Two events at the same instant: no span, no rate.
    expect(observedRate([event(1, { ts: 5 }), event(2, { ts: 5 })])).toBeNull();
  });
});

describe("diffWindows", () => {
  it("computes per-kind deltas ordered by magnitude, ties by kind", () => {
    const a = [event(1), event(2), event(3, { kind: "chat" })];
    const b = [event(4), event(5, { kind: "release" }), event(6, { kind: "release" })];
    const diff = diffWindows(a, b);
    expect(diff.kinds).toEqual([
      { kind: "release", a: 0, b: 2, delta: 2 },
      { kind: "chat", a: 1, b: 0, delta: -1 },
      { kind: "claim", a: 2, b: 1, delta: -1 },
    ]);
    expect(diff.totalA).toBe(3);
    expect(diff.totalB).toBe(3);
  });

  it("names who appeared and who went quiet, ignoring unnamed actors", () => {
    const a = [event(1, { actor: "alfa" }), event(2, { actor: "beta" }), event(3, { actor: "" })];
    const b = [event(4, { actor: "beta" }), event(5, { actor: "gama" })];
    const diff = diffWindows(a, b);
    expect(diff.appeared).toEqual(["gama"]);
    expect(diff.wentQuiet).toEqual(["alfa"]);
  });

  it("handles empty windows and carries each window's own rate", () => {
    const diff = diffWindows([], [event(1, { ts: 0 }), event(2, { ts: 120 })]);
    expect(diff.kinds).toEqual([{ kind: "claim", a: 0, b: 2, delta: 2 }]);
    expect(diff.rateA).toBeNull();
    expect(diff.rateB).toBe(1);
    expect(diff.appeared).toEqual(["a"]);
    expect(diff.wentQuiet).toEqual([]);
  });
});
