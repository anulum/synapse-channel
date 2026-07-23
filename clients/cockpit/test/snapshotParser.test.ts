// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — untrusted fleet-snapshot parser tests

import { describe, expect, it } from "vitest";

import { parseSnapshot } from "../src/lib/snapshotParser";

describe("parseSnapshot", () => {
  it("rejects non-object payloads", () => {
    expect(parseSnapshot(null)).toBeNull();
    expect(parseSnapshot("nope")).toBeNull();
    expect(parseSnapshot([1, 2, 3])).toBeNull();
    expect(parseSnapshot(42)).toBeNull();
  });

  it("fills safe empty defaults for a bare object", () => {
    const snapshot = parseSnapshot({});
    expect(snapshot).not.toBeNull();
    expect(snapshot?.online_agents).toEqual([]);
    expect(snapshot?.fleet.agents.live).toEqual([]);
    expect(snapshot?.fleet.claims.active).toBe(0);
    expect(snapshot?.risk.level).toBe("green");
    expect(snapshot?.manifest).toEqual([]);
  });

  it("parses fleet, graph, role, receipt, and risk evidence", () => {
    const snapshot = parseSnapshot({
      online_agents: ["a", "b"],
      agent_roles: { a: ["operator", 7], ignored: "admin" },
      state: { active_claims: [] },
      board: { ready: ["t1"] },
      manifest: [{ id: "cap" }],
      fleet: {
        agents: { live: ["a"], waiters: ["b-rx"], missing_waiters: ["a-rx"] },
        claims: {
          active: 1,
          stale: 1,
          active_claims: [
            {
              owner: "a",
              task_id: "t1",
              paths: ["x.py"],
              lease_expires_at: 123.5,
              git: { branch: "feat", base: "", auto_release_on: "push" },
            },
          ],
          stale_claims: [{ owner: "b", task_id: "t2", paths: ["y.py"], stale: true }],
        },
        branch_conflicts: [{ owner_a: "a", owner_b: "b" }],
        task_graph: {
          nodes: [{ task_id: "t1", title: "Build", status: "in_progress", ready: true }],
          edges: [{ from: "t0", to: "t1", satisfied: true, missing: false, from_status: "done" }],
        },
        receipts: [{ seq: 8 }],
      },
      risk: {
        level: "amber",
        signals: [{ level: "amber", category: "blocked_task", subject: "t3", detail: "waiting" }],
        safe_next_work: ["t4"],
      },
    });
    expect(snapshot?.online_agents).toEqual(["a", "b"]);
    expect(snapshot?.agent_roles).toEqual({ a: ["operator"] });
    expect(snapshot?.fleet.claims.active_claims[0]?.lease_expires_at).toBe(123.5);
    expect(snapshot?.fleet.claims.active_claims[0]?.git?.base).toBe("main");
    expect(snapshot?.fleet.claims.stale_claims[0]?.stale).toBe(true);
    expect(snapshot?.fleet.task_graph.nodes[0]).toMatchObject({ task_id: "t1", ready: true });
    expect(snapshot?.fleet.task_graph.edges[0]).toMatchObject({ from: "t0", satisfied: true });
    expect(snapshot?.fleet.receipts).toEqual([{ seq: 8 }]);
    expect(snapshot?.risk.level).toBe("amber");
    expect(snapshot?.risk.signals[0]?.subject).toBe("t3");
  });

  it("coerces lease timestamps and drops non-object git", () => {
    const snapshot = parseSnapshot({
      fleet: {
        claims: {
          active_claims: [
            { owner: "a", task_id: "t1", lease_expires_at: "200", git: 7, paths: 5 },
            { owner: "b", task_id: "t2", lease_expires_at: "later", git: null },
          ],
        },
      },
    });
    const claims = snapshot?.fleet.claims.active_claims ?? [];
    expect(claims[0]?.lease_expires_at).toBe(200);
    expect(claims[0]?.git).toBeNull();
    expect(claims[0]?.paths).toEqual([]);
    expect(claims[1]?.lease_expires_at).toBeNull();
    expect(snapshot?.fleet.claims.active).toBe(2);
  });

  it("normalises unknown risk levels to green", () => {
    expect(parseSnapshot({ risk: { level: "chartreuse" } })?.risk.level).toBe("green");
    expect(parseSnapshot({ risk: { level: "red" } })?.risk.level).toBe("red");
  });

  it("coerces non-string scalars and drops non-string list items", () => {
    const snapshot = parseSnapshot({
      online_agents: ["ok", 5, null],
      fleet: {
        claims: {
          active_claims: [
            { owner: 42, task_id: true, paths: ["a.py", 9], git: { branch: 1, auto_release_on: 2 } },
          ],
        },
      },
    });
    const claim = snapshot?.fleet.claims.active_claims[0];
    expect(snapshot?.online_agents).toEqual(["ok"]);
    expect(claim?.owner).toBe("");
    expect(claim?.task_id).toBe("");
    expect(claim?.paths).toEqual(["a.py"]);
    expect(claim?.git?.branch).toBe("");
  });

  it("carries configuration pinning fields only when they are strings", () => {
    const parsed = parseSnapshot({ hub_version: "0.98.0", config_epoch: "abc123" });
    expect(parsed?.hub_version).toBe("0.98.0");
    expect(parsed?.config_epoch).toBe("abc123");
    const malformed = parseSnapshot({ hub_version: 98, config_epoch: null });
    expect(malformed?.hub_version).toBe("");
    expect(malformed?.config_epoch).toBe("");
  });
});
