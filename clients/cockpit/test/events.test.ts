// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — event-model lane and colour mapping tests

import { describe, expect, it } from "vitest";
import { COLOUR_OF, LANES, RISK_LANE, laneOf } from "../src/lib/events";
import type { EventKind } from "../src/types";

const ALL_KINDS: readonly EventKind[] = [
  "presence",
  "claim",
  "lease",
  "release",
  "task",
  "chat",
  "finding",
  "conflict",
];

describe("event model", () => {
  it("maps every kind to a lane and a colour token", () => {
    for (const kind of ALL_KINDS) {
      expect(LANES).toContain(laneOf(kind));
      expect(COLOUR_OF[kind]).toMatch(/^var\(--/);
    }
  });

  it("routes the risk-bearing kind to the risk lane and chatter away from it", () => {
    expect(laneOf("conflict")).toBe(RISK_LANE);
    expect(laneOf("presence")).toBe("presence");
    expect(laneOf("release")).toBe("claims");
    expect(laneOf("finding")).toBe("task");
  });

  it("keeps the risk lane reserved: no routine kind rides it", () => {
    const routine = ALL_KINDS.filter((kind) => kind !== "conflict");
    for (const kind of routine) {
      expect(laneOf(kind)).not.toBe(RISK_LANE);
    }
  });

  it("renders the four lanes top-to-bottom with risk last (peripheral scan line)", () => {
    expect(LANES).toEqual(["presence", "claims", "task", "risk"]);
  });
});
