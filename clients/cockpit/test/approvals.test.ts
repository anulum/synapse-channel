// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — pending-approvals parsing tests

import { describe, expect, it } from "vitest";

import { parsePendingApprovals } from "../src/lib/approvals";
import { parseSnapshot } from "../src/lib/snapshot";
import type { FleetSnapshot } from "../src/types";

function snapshotWithState(state: Record<string, unknown>): FleetSnapshot {
  const parsed = parseSnapshot({ state });
  if (parsed === null) throw new Error("fixture snapshot must parse");
  return parsed;
}

describe("parsePendingApprovals", () => {
  it("returns empty before the first snapshot", () => {
    expect(parsePendingApprovals(null)).toEqual([]);
  });

  it("returns empty when the hub predates the surface (field absent)", () => {
    expect(parsePendingApprovals(snapshotWithState({}))).toEqual([]);
  });

  it("returns empty when the field is not a list", () => {
    expect(
      parsePendingApprovals(snapshotWithState({ pending_relay_approvals: "3 pending" })),
    ).toEqual([]);
  });

  it("preserves the server's oldest-first order without re-sorting", () => {
    const pending = parsePendingApprovals(
      snapshotWithState({
        pending_relay_approvals: [
          { action: "task_update", namespace: "quantum", task_id: "z-9", requester: "op-a" },
          { action: "message", namespace: "fusion", task_id: "a-1", requester: "op-b" },
        ],
      }),
    );
    expect(pending).toEqual([
      { action: "task_update", namespace: "quantum", taskId: "z-9", requester: "op-a" },
      { action: "message", namespace: "fusion", taskId: "a-1", requester: "op-b" },
    ]);
  });

  it("skips entries that are not records and defaults malformed fields", () => {
    const pending = parsePendingApprovals(
      snapshotWithState({
        pending_relay_approvals: [
          "not a record",
          null,
          ["also", "not"],
          { action: 7, namespace: null, task_id: ["x"], requester: { name: "op" } },
        ],
      }),
    );
    expect(pending).toEqual([{ action: "", namespace: "", taskId: "", requester: "" }]);
  });
});
