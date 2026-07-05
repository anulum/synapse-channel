// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — claims-board derivation and countdown tests

import { describe, expect, it } from "vitest";
import { deriveClaims, formatCountdown, parseConflicts } from "../src/lib/claims";
import type { ClaimRecord, FleetSnapshot } from "../src/types";

function claim(
  owner: string,
  taskId: string,
  overrides: Partial<ClaimRecord> = {},
): ClaimRecord {
  return {
    task_id: taskId,
    owner,
    lease_expires_at: null,
    paths: [],
    stale: false,
    git: null,
    ...overrides,
  };
}

function snapshotOf(options: {
  active?: readonly ClaimRecord[];
  stale?: readonly ClaimRecord[];
  conflicts?: readonly Record<string, unknown>[];
}): FleetSnapshot {
  const active = options.active ?? [];
  const stale = options.stale ?? [];
  return {
    hub_version: "",
    config_epoch: "",
    online_agents: [],
    state: {},
    board: {},
    manifest: [],
    fleet: {
      agents: { live: [], waiters: [], missing_waiters: [] },
      claims: {
        active: active.length,
        stale: stale.length,
        active_claims: active,
        stale_claims: stale,
      },
      branch_conflicts: options.conflicts ?? [],
      task_graph: { nodes: [], edges: [] },
      receipts: [],
    },
    risk: { level: "green", signals: [], safe_next_work: [] },
  };
}

describe("parseConflicts", () => {
  it("reads every field of a complete conflict record", () => {
    const [conflict] = parseConflicts(
      snapshotOf({
        conflicts: [
          {
            owner_a: "a",
            branch_a: "feature/x",
            base_a: "main",
            owner_b: "b",
            branch_b: "feature/y",
            base_b: "main",
            paths: ["src/kernel.rs", 7, "src/api.py"],
            description: "feature/x vs feature/y overlap",
          },
        ],
      }),
    );
    expect(conflict).toEqual({
      ownerA: "a",
      branchA: "feature/x",
      baseA: "main",
      ownerB: "b",
      branchB: "feature/y",
      baseB: "main",
      paths: ["src/kernel.rs", "src/api.py"],
      description: "feature/x vs feature/y overlap",
    });
  });

  it("tolerates a partial record from an older hub", () => {
    const [conflict] = parseConflicts(snapshotOf({ conflicts: [{ owner_a: "solo" }] }));
    expect(conflict).toEqual({
      ownerA: "solo",
      branchA: "",
      baseA: "",
      ownerB: "",
      branchB: "",
      baseB: "",
      paths: [],
      description: "",
    });
  });
});

describe("deriveClaims", () => {
  it("returns nothing before the first snapshot", () => {
    expect(deriveClaims(null, 1_000)).toEqual([]);
  });

  it("ranks conflict rows first, then stale, then held by soonest lease", () => {
    const nowMs = 100_000; // epoch seconds 100
    const views = deriveClaims(
      snapshotOf({
        active: [
          claim("calm", "open-ended"),
          claim("calm", "later", { lease_expires_at: 400 }),
          claim("calm", "soon", { lease_expires_at: 160 }),
          claim("fighter", "contested", { lease_expires_at: 500 }),
        ],
        stale: [claim("sleeper", "forgotten", { lease_expires_at: 90, stale: true })],
        conflicts: [{ owner_a: "fighter", owner_b: "elsewhere" }],
      }),
      nowMs,
    );
    expect(views.map((view) => [view.claim.task_id, view.urgency])).toEqual([
      ["contested", "conflict"],
      ["forgotten", "stale"],
      ["soon", "held"],
      ["later", "held"],
      ["open-ended", "held"],
    ]);
    expect(views.map((view) => view.secondsToExpiry)).toEqual([400, -10, 60, 300, null]);
    expect(views[0]?.inConflict).toBe(true);
    expect(views[2]?.inConflict).toBe(false);
  });

  it("marks both sides of a conflict and breaks expiry ties by task id", () => {
    const views = deriveClaims(
      snapshotOf({
        active: [
          claim("b-side", "beta", { lease_expires_at: 200 }),
          claim("a-side", "alpha", { lease_expires_at: 200 }),
        ],
        conflicts: [{ owner_a: "a-side", owner_b: "b-side" }],
      }),
      100_000,
    );
    expect(views.map((view) => view.claim.task_id)).toEqual(["alpha", "beta"]);
    expect(views.every((view) => view.urgency === "conflict")).toBe(true);
  });
});

describe("formatCountdown", () => {
  it("formats the open-ended, running, boundary, and overdue cases", () => {
    expect(formatCountdown(null)).toBe("no lease");
    expect(formatCountdown(725)).toBe("12:05");
    expect(formatCountdown(0)).toBe("0:00");
    expect(formatCountdown(-42)).toBe("-0:42");
    expect(formatCountdown(3600)).toBe("60:00");
    expect(formatCountdown(9.9)).toBe("0:09");
  });
});
