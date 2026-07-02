// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — risk-signal triage ordering tests

import { describe, expect, it } from "vitest";
import { orderSignals } from "../src/lib/risk";
import type { RiskSignal } from "../src/types";

function signal(level: RiskSignal["level"], category: string, subject: string): RiskSignal {
  return { level, category, subject, detail: "" };
}

describe("orderSignals", () => {
  it("orders red before amber before green, then category, then subject", () => {
    const shuffled = [
      signal("green", "ok", "a"),
      signal("amber", "lease", "b"),
      signal("red", "stale_claim", "z"),
      signal("red", "conflict", "y"),
      signal("red", "conflict", "x"),
    ];
    expect(orderSignals(shuffled).map((item) => [item.level, item.category, item.subject])).toEqual([
      ["red", "conflict", "x"],
      ["red", "conflict", "y"],
      ["red", "stale_claim", "z"],
      ["amber", "lease", "b"],
      ["green", "ok", "a"],
    ]);
  });

  it("does not mutate the input and handles the empty list", () => {
    const input = [signal("amber", "lease", "b"), signal("red", "conflict", "a")];
    const snapshotBefore = [...input];
    orderSignals(input);
    expect(input).toEqual(snapshotBefore);
    expect(orderSignals([])).toEqual([]);
  });
});
