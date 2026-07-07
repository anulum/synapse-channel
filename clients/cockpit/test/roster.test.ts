// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — fleet roster derivation tests

import { describe, expect, it } from "vitest";
import { deriveRoster } from "../src/lib/roster";
import { parseSnapshot } from "../src/lib/snapshot";
import type { FleetSnapshot } from "../src/types";

/** Build a snapshot through the real parser so tests exercise the true shape. */
function snapshotOf(raw: Record<string, unknown>): FleetSnapshot {
  const parsed = parseSnapshot(raw);
  if (parsed === null) throw new Error("fixture did not parse");
  return parsed;
}

describe("deriveRoster", () => {
  it("returns nothing for a null snapshot", () => {
    expect(deriveRoster(null)).toEqual([]);
  });

  it("marks a live agent with no claims idle", () => {
    const snapshot = snapshotOf({
      online_agents: ["alpha/aster"],
      fleet: { agents: { live: ["alpha/aster"], waiters: [], missing_waiters: [] } },
    });
    const rows = deriveRoster(snapshot);
    expect(rows).toHaveLength(1);
    expect(rows[0]?.status).toBe("idle");
    expect(rows[0]?.online).toBe(true);
    expect(rows[0]?.paths).toEqual([]);
  });

  it("flags exactly the agent whose -rx waiter the hub reports missing", () => {
    const snapshot = snapshotOf({
      online_agents: ["alpha/aster", "beta/briar"],
      fleet: {
        agents: {
          live: ["alpha/aster", "beta/briar"],
          waiters: ["beta/briar-rx"],
          missing_waiters: ["alpha/aster-rx"],
        },
      },
    });
    const byName = new Map(deriveRoster(snapshot).map((row) => [row.agent, row]));
    expect(byName.get("alpha/aster")?.wakerMissing).toBe(true);
    expect(byName.get("beta/briar")?.wakerMissing).toBe(false);
  });

  it("classifies holding, stale, and conflict, worst-first", () => {
    const snapshot = snapshotOf({
      online_agents: ["alpha/aster", "beta/briar", "gamma/cedar"],
      fleet: {
        agents: {
          live: ["alpha/aster", "beta/briar", "gamma/cedar"],
          waiters: [],
          missing_waiters: [],
        },
        claims: {
          active_claims: [
            { owner: "alpha/aster", task_id: "t1", paths: ["a.py"] },
            { owner: "gamma/cedar", task_id: "t3", paths: ["c.py"] },
          ],
          stale_claims: [{ owner: "beta/briar", task_id: "t2", paths: ["b.py"], stale: true }],
        },
        branch_conflicts: [{ owner_a: "gamma/cedar", owner_b: "alpha/aster" }],
      },
    });
    const rows = deriveRoster(snapshot);
    // gamma is in conflict (worst); beta is stale; alpha holds but is also in the
    // conflict, so it too is conflict — ordering is by status then name.
    expect(rows.map((row) => row.agent)).toEqual([
      "alpha/aster",
      "gamma/cedar",
      "beta/briar",
    ]);
    expect(rows[0]?.status).toBe("conflict");
    expect(rows[1]?.status).toBe("conflict");
    expect(rows[2]?.status).toBe("stale");
  });

  it("keeps a stale claim from a departed owner visible and offline", () => {
    const snapshot = snapshotOf({
      online_agents: ["alpha/aster"],
      fleet: {
        agents: { live: ["alpha/aster"], waiters: [], missing_waiters: [] },
        claims: {
          active_claims: [],
          stale_claims: [{ owner: "ghost/briar", task_id: "t9", paths: ["z.py"], stale: true }],
        },
      },
    });
    const rows = deriveRoster(snapshot);
    const ghost = rows.find((row) => row.agent === "ghost/briar");
    expect(ghost?.status).toBe("stale");
    expect(ghost?.online).toBe(false);
    expect(ghost?.staleClaims).toHaveLength(1);
  });

  it("unions, dedupes, and sorts held paths across active and stale claims", () => {
    const snapshot = snapshotOf({
      online_agents: ["alpha/aster"],
      fleet: {
        agents: { live: ["alpha/aster"], waiters: [], missing_waiters: [] },
        claims: {
          active_claims: [{ owner: "alpha/aster", task_id: "t1", paths: ["b.py", "a.py"] }],
          stale_claims: [{ owner: "alpha/aster", task_id: "t2", paths: ["a.py", "c.py"], stale: true }],
        },
      },
    });
    const rows = deriveRoster(snapshot);
    expect(rows[0]?.paths).toEqual(["a.py", "b.py", "c.py"]);
    // A stale claim outranks the active one for the row's status.
    expect(rows[0]?.status).toBe("stale");
  });

  it("folds -rx waiters out of the rows (they ride the header count)", () => {
    const snapshot = snapshotOf({
      online_agents: ["alpha/aster", "alpha/aster-rx"],
      fleet: {
        agents: { live: ["alpha/aster"], waiters: ["alpha/aster-rx"], missing_waiters: [] },
      },
    });
    const rows = deriveRoster(snapshot);
    expect(rows.map((row) => row.agent)).toEqual(["alpha/aster"]);
  });

  it("groups multiple claims held by the same owner", () => {
    const snapshot = snapshotOf({
      online_agents: ["alpha/aster"],
      fleet: {
        agents: { live: ["alpha/aster"], waiters: [], missing_waiters: [] },
        claims: {
          active_claims: [
            { owner: "alpha/aster", task_id: "t1", paths: ["a.py"] },
            { owner: "alpha/aster", task_id: "t2", paths: ["b.py"] },
          ],
          stale_claims: [],
        },
      },
    });
    const rows = deriveRoster(snapshot);
    expect(rows[0]?.activeClaims).toHaveLength(2);
    expect(rows[0]?.paths).toEqual(["a.py", "b.py"]);
  });

  it("ignores conflict entries whose owners are not strings", () => {
    const snapshot = snapshotOf({
      online_agents: ["alpha/aster"],
      fleet: {
        agents: { live: ["alpha/aster"], waiters: [], missing_waiters: [] },
        claims: {
          active_claims: [{ owner: "alpha/aster", task_id: "t1", paths: ["a.py"] }],
          stale_claims: [],
        },
        branch_conflicts: [{ owner_a: 7, owner_b: null }],
      },
    });
    const rows = deriveRoster(snapshot);
    expect(rows[0]?.inConflict).toBe(false);
    expect(rows[0]?.status).toBe("holding");
  });

  it("skips empty-string owners", () => {
    const snapshot = snapshotOf({
      online_agents: [],
      fleet: {
        agents: { live: [], waiters: [], missing_waiters: [] },
        claims: {
          active_claims: [{ owner: "", task_id: "t0", paths: ["x.py"] }],
          stale_claims: [],
        },
      },
    });
    expect(deriveRoster(snapshot)).toEqual([]);
  });

  it("counts a claim owner that is not in the live roster as present via online_agents", () => {
    const snapshot = snapshotOf({
      online_agents: ["alpha/aster"],
      fleet: {
        agents: { live: [], waiters: [], missing_waiters: [] },
        claims: {
          active_claims: [{ owner: "alpha/aster", task_id: "t1", paths: ["a.py"] }],
          stale_claims: [],
        },
      },
    });
    const rows = deriveRoster(snapshot);
    expect(rows[0]?.online).toBe(true);
    expect(rows[0]?.status).toBe("holding");
  });
});

describe("deriveRoster roles", () => {
  it("binds the hub's roles to their rows and defaults the unbound to none", () => {
    const snapshot = parseSnapshot({
      online_agents: ["a", "b"],
      agent_roles: { a: ["reviewer", 7, "captain"], b: "not-a-list" },
      fleet: { agents: { live: ["a", "b"] } },
    });
    const rows = deriveRoster(snapshot);
    const byName = new Map(rows.map((row) => [row.agent, row.roles]));
    expect(byName.get("a")).toEqual(["reviewer", "captain"]);
    expect(byName.get("b")).toEqual([]);
  });
});
