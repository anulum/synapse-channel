// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — untrusted fleet-snapshot parser tests

import { describe, expect, it } from "vitest";

import { parseBounds, parseHeadline, parseSnapshot } from "../src/lib/snapshotParser";

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

describe("parseBounds", () => {
  const block = {
    closed_included: false,
    tasks_total: 558,
    tasks_sent: 143,
    closed_omitted: 415,
    progress_total: 5000,
    progress_sent: 200,
    receipts_total: 60,
    receipts_sent: 50,
  };

  it("reads every exact total of a bounded snapshot", () => {
    expect(parseBounds(block)).toEqual({
      closedIncluded: false,
      tasksTotal: 558,
      tasksSent: 143,
      closedOmitted: 415,
      progressTotal: 5000,
      progressSent: 200,
      receiptsTotal: 60,
      receiptsSent: 50,
    });
    expect(parseBounds({ ...block, closed_included: true })?.closedIncluded).toBe(true);
    expect(parseSnapshot({ bounds: block })?.bounds?.closedOmitted).toBe(415);
  });

  it("is null for an older dashboard or a malformed block, never a guessed count", () => {
    expect(parseSnapshot({})?.bounds).toBeNull();
    for (const value of [null, "x", [1], { ...block, tasks_total: -1 }, { ...block, progress_sent: 1.5 }]) {
      expect(parseBounds(value)).toBeNull();
    }
    const { receipts_sent: _dropped, ...partial } = block;
    expect(parseBounds(partial)).toBeNull();
  });
});

describe("parseHeadline", () => {
  const block = {
    agents_live: 2,
    waiters_missing: 1,
    claims_active: 1,
    claims_stale: 0,
    tasks_open: 7,
    tasks_in_progress: 2,
    tasks_ready: 1,
    tasks_blocked: 5,
    tasks_blocked_marked: 2,
    tasks_blocked_waiting: 3,
    tasks_closed: 2,
    risk_signals: 3,
    risk_signals_red: 1,
    risk_signals_amber: 2,
    branch_conflicts: 0,
    peers_reachable: 0,
    peers_total: 0,
    seats_listening: 3,
    seats_active_not_listening: 1,
    seats_silent: 2,
  };

  it("reads every counter of the hub's block under its camel-case name", () => {
    expect(parseSnapshot({ headline: block })?.headline).toEqual({
      agentsLive: 2,
      waitersMissing: 1,
      claimsActive: 1,
      claimsStale: 0,
      tasksOpen: 7,
      tasksInProgress: 2,
      tasksReady: 1,
      tasksBlocked: 5,
      tasksBlockedMarked: 2,
      tasksBlockedWaiting: 3,
      tasksClosed: 2,
      riskSignals: 3,
      riskSignalsRed: 1,
      riskSignalsAmber: 2,
      branchConflicts: 0,
      peersReachable: 0,
      peersTotal: 0,
      seatsListening: 3,
      seatsActiveNotListening: 1,
      seatsSilent: 2,
    });
  });

  it("refuses a block with any counter missing or malformed", () => {
    expect(parseSnapshot({})?.headline).toBeNull();
    for (const value of [null, "x", [1], { ...block, tasks_blocked: -1 }, { ...block, risk_signals: 1.5 }]) {
      expect(parseHeadline(value)).toBeNull();
    }
    const { peers_total: _dropped, ...partial } = block;
    expect(parseHeadline(partial)).toBeNull();
  });
});

describe("fleet seats", () => {
  it("keeps every well-formed seat row and drops the rest", () => {
    const seats = parseSnapshot({
      fleet: {
        seats: [
          { identity: "a/one", listening: true, active: true, evidence: "note", evidence_at: 12.5, state: "listening" },
          { identity: "b/two", listening: false, active: true, evidence: "claim", evidence_at: null, state: "active, not listening" },
          { identity: "c/three", listening: "yes", active: 1, evidence: "rumour", state: "silent" },
          { identity: "", state: "silent" },
          { identity: "d/four", state: "offline" },
          "e/five",
        ],
      },
    })?.fleet.seats;
    expect(seats).toEqual([
      { identity: "a/one", listening: true, active: true, evidence: "note", evidenceAt: 12.5, state: "listening" },
      { identity: "b/two", listening: false, active: true, evidence: "claim", evidenceAt: null, state: "active, not listening" },
      { identity: "c/three", listening: false, active: false, evidence: null, evidenceAt: null, state: "silent" },
    ]);
    expect(parseSnapshot({})?.fleet.seats).toEqual([]);
  });
});
