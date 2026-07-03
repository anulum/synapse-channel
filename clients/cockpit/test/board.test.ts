// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — task-board derivation and findings-stream tests

import { describe, expect, it } from "vitest";
import { boardTruncation, deriveBoard, deriveFindings } from "../src/lib/board";
import { parseSnapshot } from "../src/lib/snapshot";
import type { FleetSnapshot, TaskGraphEdge, TaskGraphNode } from "../src/types";

function node(taskId: string, status: string, ready = false, title = ""): TaskGraphNode {
  return { task_id: taskId, title, status, ready };
}

function edge(
  from: string,
  to: string,
  overrides: Partial<TaskGraphEdge> = {},
): TaskGraphEdge {
  return { from, to, satisfied: false, missing: false, from_status: "open", ...overrides };
}

function snapshotOf(options: {
  nodes?: readonly TaskGraphNode[];
  edges?: readonly TaskGraphEdge[];
  board?: Record<string, unknown>;
}): FleetSnapshot {
  return {
    online_agents: [],
    state: {},
    board: options.board ?? {},
    manifest: [],
    fleet: {
      agents: { live: [], waiters: [], missing_waiters: [] },
      claims: { active: 0, stale: 0, active_claims: [], stale_claims: [] },
      branch_conflicts: [],
      task_graph: { nodes: options.nodes ?? [], edges: options.edges ?? [] },
      receipts: [],
    },
    risk: { level: "green", signals: [], safe_next_work: [] },
  };
}

describe("deriveBoard", () => {
  it("returns nothing before the first snapshot", () => {
    expect(deriveBoard(null)).toEqual([]);
  });

  it("buckets tasks blocked → ready → open → done and sorts by id inside buckets", () => {
    const rows = deriveBoard(
      snapshotOf({
        nodes: [
          node("z-done", "done"),
          node("m-open", "open"),
          node("a-ready", "open", true),
          node("gated", "open", false),
          node("b-open", "claimed"),
        ],
        edges: [edge("z-done", "gated", { satisfied: false, from_status: "done" })],
      }),
    );
    expect(rows.map((row) => [row.taskId, row.bucket])).toEqual([
      ["gated", "blocked"],
      ["a-ready", "ready"],
      ["b-open", "open"],
      ["m-open", "open"],
      ["z-done", "done"],
    ]);
  });

  it("prefers blocked over ready when a ready-flagged task still has an unmet edge", () => {
    const rows = deriveBoard(
      snapshotOf({
        nodes: [node("both", "open", true)],
        edges: [edge("gone", "both", { missing: true, from_status: "missing" })],
      }),
    );
    expect(rows[0]?.bucket).toBe("blocked");
    expect(rows[0]?.dependsOn).toEqual([
      { taskId: "gone", satisfied: false, missing: true, status: "missing" },
    ]);
  });

  it("collects several prerequisites per task and several dependents per prerequisite", () => {
    const rows = deriveBoard(
      snapshotOf({
        nodes: [node("gate", "done"), node("first", "open"), node("second", "open")],
        edges: [
          edge("gate", "first", { satisfied: true, from_status: "done" }),
          edge("gate", "second", { satisfied: true, from_status: "done" }),
          edge("other", "first", { missing: true, from_status: "missing" }),
        ],
      }),
    );
    const byId = new Map(rows.map((row) => [row.taskId, row]));
    expect(byId.get("gate")?.unblocks).toEqual(["first", "second"]);
    expect(byId.get("first")?.dependsOn.map((chip) => chip.taskId)).toEqual(["gate", "other"]);
    expect(byId.get("first")?.bucket).toBe("blocked");
    expect(byId.get("second")?.bucket).toBe("open");
  });

  it("honours a board-declared blocked status even with no unmet edge", () => {
    const rows = deriveBoard(snapshotOf({ nodes: [node("held-back", "blocked")] }));
    expect(rows[0]?.bucket).toBe("blocked");
  });

  it("keeps a task with only satisfied prerequisites out of the blocked bucket", () => {
    const rows = deriveBoard(
      snapshotOf({
        nodes: [node("free", "open", true)],
        edges: [edge("gate", "free", { satisfied: true, from_status: "done" })],
      }),
    );
    expect(rows[0]?.bucket).toBe("ready");
  });
});

describe("boardTruncation", () => {
  it("reads the hub's cap signal and defaults honestly without one", () => {
    expect(boardTruncation(null)).toEqual({ totalTasks: null, truncated: false });
    expect(boardTruncation(snapshotOf({ board: {} }))).toEqual({
      totalTasks: null,
      truncated: false,
    });
    expect(
      boardTruncation(snapshotOf({ board: { total_tasks: 977, truncated: true } })),
    ).toEqual({ totalTasks: 977, truncated: true });
    expect(
      boardTruncation(snapshotOf({ board: { total_tasks: "junk", truncated: "yes" } })),
    ).toEqual({ totalTasks: null, truncated: false });
  });
});

describe("deriveFindings", () => {
  it("returns nothing before the first snapshot or without a progress list", () => {
    expect(deriveFindings(null)).toEqual([]);
    expect(deriveFindings(snapshotOf({ board: { progress: "corrupt" } }))).toEqual([]);
  });

  it("keeps only finding-kind notes, newest first, tolerating malformed entries", () => {
    const findings = deriveFindings(
      snapshotOf({
        board: {
          progress: [
            { author: "a", kind: "finding", task_id: "t1", text: "older", posted_at: 100 },
            { author: "b", kind: "note", task_id: "t2", text: "routine" },
            "not-a-record",
            { author: "c", kind: "finding", text: "newer, no task, no stamp" },
          ],
        },
      }),
    );
    expect(findings).toEqual([
      { author: "c", taskId: "", text: "newer, no task, no stamp", postedAt: null },
      { author: "a", taskId: "t1", text: "older", postedAt: 100 },
    ]);
  });
});

describe("parseSnapshot task-graph section", () => {
  it("parses nodes, edges, and receipts with strict flag narrowing", () => {
    const parsed = parseSnapshot({
      fleet: {
        task_graph: {
          nodes: [
            { task_id: "t1", title: "unit", status: "open", ready: true },
            { task_id: "t2", ready: "yes" },
          ],
          edges: [
            { from: "t1", to: "t2", satisfied: true, missing: false, from_status: "open" },
            { from: "t0", to: "t1", satisfied: "true" },
          ],
        },
        receipts: [{ task_id: "t1" }, "junk"],
      },
    });
    expect(parsed?.fleet.task_graph.nodes).toEqual([
      { task_id: "t1", title: "unit", status: "open", ready: true },
      { task_id: "t2", title: "", status: "", ready: false },
    ]);
    expect(parsed?.fleet.task_graph.edges).toEqual([
      { from: "t1", to: "t2", satisfied: true, missing: false, from_status: "open" },
      { from: "t0", to: "t1", satisfied: false, missing: false, from_status: "" },
    ]);
    expect(parsed?.fleet.receipts).toEqual([{ task_id: "t1" }, {}]);
  });

  it("defaults to an empty graph when the fleet section omits it", () => {
    const parsed = parseSnapshot({});
    expect(parsed?.fleet.task_graph).toEqual({ nodes: [], edges: [] });
    expect(parsed?.fleet.receipts).toEqual([]);
  });
});
