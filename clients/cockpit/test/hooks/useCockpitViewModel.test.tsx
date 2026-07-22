// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — deterministic cockpit projection hook tests

import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ReplaySlot } from "../../src/components/ReplayWorkbench";
import type { CockpitFeeds } from "../../src/hooks/useCockpitFeeds";
import { useCockpitViewModel } from "../../src/hooks/useCockpitViewModel";
import type { FleetSnapshot } from "../../src/types";

const CLAIM = {
  task_id: "TASK-1",
  owner: "alpha/one",
  lease_expires_at: 200,
  paths: ["src/a.ts"],
  stale: false,
  git: null,
} as const;

const SNAPSHOT: FleetSnapshot = {
  online_agents: ["alpha/one"],
  agent_roles: { "alpha/one": ["worker"] },
  hub_version: "1.0.0",
  config_epoch: "epoch-a",
  state: {
    dead_letters: [{ target: "ghost", count: 2, last_sender: "alpha/one", last_ts: 20 }],
    pending_relay_approvals: [
      { action: "task_update", namespace: "alpha", task_id: "TASK-1", requester: "operator" },
    ],
  },
  board: {
    progress: [
      { kind: "finding", author: "alpha/one", task_id: "TASK-1", text: "evidence", posted_at: 19 },
    ],
  },
  manifest: [],
  fleet: {
    agents: {
      live: ["alpha/one"],
      waiters: ["alpha/one-rx"],
      missing_waiters: ["alpha/one-rx"],
    },
    claims: {
      active: 1,
      stale: 0,
      active_claims: [CLAIM],
      stale_claims: [],
    },
    branch_conflicts: [],
    task_graph: {
      nodes: [{ task_id: "TASK-1", title: "Primary", status: "open", ready: true }],
      edges: [],
    },
    receipts: [],
  },
  risk: { level: "green", signals: [], safe_next_work: [] },
};

const ABSENT = {
  data: null,
  status: "absent",
  fetchedAt: null,
  error: null,
} as const;

function feeds(snapshot: FleetSnapshot | null): CockpitFeeds {
  return {
    snap: {
      snapshot,
      status: snapshot === null ? "connecting" : "live",
      fetchedAt: snapshot === null ? null : 1,
      error: null,
    },
    stamp: "00:00:00",
    kpis: [],
    log: [{
      seq: 7,
      ts: 20,
      kind: "chat",
      lane: "task",
      severity: 0.4,
      actor: "alpha/one",
      label: "coordination message",
      taskId: "TASK-1",
      payload: { type: "chat", sender: "alpha/one", target: "beta/two" },
    }],
    spineSource: undefined,
    provenance: "hub",
    coverage: {
      source: "hub",
      retained: 1,
      capacity: 250,
      minSeq: 7,
      maxSeq: 7,
      minTs: 20,
      maxTs: 20,
      atCapacity: false,
    },
    nowMs: 100_000,
    reliability: ABSENT,
    federation: ABSENT,
    metrics: ABSENT,
    sessions: ABSENT,
    waits: {
      data: {
        present: true,
        waits: [{
          taskId: "TASK-1",
          title: "Primary",
          who: "alpha/one",
          onWhat: ["OWNER"],
          since: 10,
          status: "blocked",
        }],
        waitCount: 1,
        logEndSeq: 7,
        note: "",
      },
      status: "live",
      fetchedAt: 1,
      error: null,
    },
    anomalyReport: ABSENT,
    receipts: ABSENT,
    operatorActions: ABSENT,
    transport: { status: "live", attempt: 1, detail: null },
  };
}

describe("useCockpitViewModel", () => {
  it("projects live evidence and swaps only replay-supported claims and tasks", () => {
    const replaySlot: ReplaySlot = {
      seq: 4,
      note: null,
      state: {
        asOfSeq: 4,
        logEndSeq: 7,
        asOfTs: 15,
        note: "claims and tasks only",
        claims: [],
        tasks: [{
          taskId: "TASK-OLD",
          title: "Historical",
          status: "done",
          bucket: "done",
          dependsOn: [],
          unblocks: [],
        }],
      },
    };
    const { result, rerender } = renderHook(
      ({ travelling }: { readonly travelling: boolean }) => useCockpitViewModel({
        feeds: feeds(SNAPSHOT),
        replaySlot,
        travelling,
        focus: "alpha",
        brush: null,
      }),
      { initialProps: { travelling: false } },
    );

    expect(result.current.connected).toBe(true);
    expect(result.current.roster.map((row) => row.agent)).toEqual(["alpha/one"]);
    expect(result.current.waiters).toBe(1);
    expect(result.current.claims).toHaveLength(1);
    expect(result.current.board.map((task) => task.taskId)).toEqual(["TASK-1"]);
    expect(result.current.communication.messages).toBe(1);
    expect(result.current.findings.map((finding) => finding.text)).toEqual(["evidence"]);
    expect(result.current.deadLetters.map((letter) => letter.target)).toEqual(["ghost"]);
    expect(result.current.approvals.map((approval) => approval.taskId)).toEqual(["TASK-1"]);
    expect(result.current.attention.some((item) => item.kind === "missing_waiter")).toBe(true);

    rerender({ travelling: true });
    expect(result.current.claims).toEqual([]);
    expect(result.current.board.map((task) => task.taskId)).toEqual(["TASK-OLD"]);
    expect(result.current.conflicts).toEqual([]);
    expect(result.current.roster.map((row) => row.agent)).toEqual(["alpha/one"]);
  });

  it("returns an honest disconnected empty model before the first snapshot", () => {
    const disconnectedFeeds = { ...feeds(null), waits: ABSENT };
    const { result } = renderHook(() => useCockpitViewModel({
      feeds: disconnectedFeeds,
      replaySlot: null,
      travelling: false,
      focus: "",
      brush: null,
    }));
    expect(result.current.connected).toBe(false);
    expect(result.current.roster).toEqual([]);
    expect(result.current.claims).toEqual([]);
    expect(result.current.board).toEqual([]);
  });
});
