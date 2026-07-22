// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — deterministic fleet attention queue tests

import { describe, expect, it } from "vitest";

import { deriveAttentionQueue, type AttentionInputs } from "../src/lib/attention";

const EMPTY: AttentionInputs = {
  conflicts: [],
  deadLetters: [],
  communication: { nodes: [], edges: [], projects: [], messages: 0 },
  claims: [],
  missingWaiters: [],
  board: [],
  approvals: [],
  waits: [],
};

describe("deriveAttentionQueue", () => {
  it("keeps an empty evidence set empty", () => {
    expect(deriveAttentionQueue(EMPTY)).toEqual([]);
  });

  it("merges every supported source in documented categorical order", () => {
    const items = deriveAttentionQueue({
      conflicts: [
        {
          ownerA: "alpha/one",
          branchA: "a",
          baseA: "main",
          ownerB: "beta/two",
          branchB: "b",
          baseB: "main",
          paths: ["src/shared.ts"],
          description: "",
        },
      ],
      deadLetters: [
        { target: "quiet/peer", count: 2, lastSender: "alpha/one", lastTs: 40 },
        { target: "ignored", count: 0, lastSender: "", lastTs: null },
      ],
      communication: {
        nodes: [],
        projects: [],
        messages: 5,
        edges: [
          {
            id: "alpha\0beta",
            source: "alpha/one",
            target: "beta/two",
            messages: 3,
            delivered: 0,
            deferred: 0,
            failed: 2,
            lastTs: 50,
            health: "failed",
          },
          {
            id: "beta\0gamma",
            source: "beta/two",
            target: "gamma/three",
            messages: 1,
            delivered: 0,
            deferred: 1,
            failed: 0,
            lastTs: 60,
            health: "deferred",
          },
          {
            id: "healthy",
            source: "gamma/three",
            target: "alpha/one",
            messages: 1,
            delivered: 1,
            deferred: 0,
            failed: 0,
            lastTs: 70,
            health: "healthy",
          },
        ],
      },
      claims: [
        {
          claim: {
            task_id: "claim-stale",
            owner: "alpha/one",
            lease_expires_at: 1,
            paths: ["src/a.ts"],
            stale: true,
            git: null,
          },
          urgency: "stale",
          inConflict: false,
          secondsToExpiry: -1,
        },
        {
          claim: {
            task_id: "claim-held",
            owner: "beta/two",
            lease_expires_at: null,
            paths: [],
            stale: false,
            git: null,
          },
          urgency: "held",
          inConflict: false,
          secondsToExpiry: null,
        },
      ],
      missingWaiters: ["alpha/one-rx", "  "],
      board: [
        {
          taskId: "blocked-a",
          title: "Blocked",
          status: "blocked",
          bucket: "blocked",
          dependsOn: [{ taskId: "gate-a", satisfied: false, missing: false, status: "open" }],
          unblocks: [],
        },
        {
          taskId: "ready-a",
          title: "Ready",
          status: "open",
          bucket: "ready",
          dependsOn: [],
          unblocks: [],
        },
      ],
      approvals: [
        { action: "task_update", namespace: "SYNAPSE-CHANNEL", taskId: "approve-a", requester: "operator-a" },
      ],
      waits: [
        {
          taskId: "wait-a",
          title: "Wait",
          who: "alpha/one",
          onWhat: ["gate-b"],
          since: 10,
          status: "blocked",
        },
      ],
    });

    expect(items.map((item) => item.kind)).toEqual([
      "branch_conflict",
      "dead_letter",
      "failed_route",
      "stale_claim",
      "missing_waiter",
      "blocked_task",
      "deferred_route",
      "pending_approval",
      "coordination_wait",
    ]);
    expect(items[0]?.evidence).toBe("1 overlapping path");
    expect(items[2]?.action).toEqual({
      kind: "route",
      source: "alpha/one",
      target: "beta/two",
    });
    expect(items.at(-1)?.observedAt).toBe(10);
  });

  it("uses oldest evidence and stable ids within one kind", () => {
    const items = deriveAttentionQueue({
      ...EMPTY,
      deadLetters: [
        { target: "zeta", count: 1, lastSender: "", lastTs: null },
        { target: "beta", count: 1, lastSender: "", lastTs: 20 },
        { target: "alpha", count: 1, lastSender: "", lastTs: 20 },
      ],
    });
    expect(items.map((item) => item.subject)).toEqual(["alpha", "beta", "zeta"]);
  });

  it("preserves evidence-safe fallbacks and nullable actions", () => {
    const items = deriveAttentionQueue({
      ...EMPTY,
      conflicts: [
        {
          ownerA: "",
          branchA: "",
          baseA: "",
          ownerB: "",
          branchB: "",
          baseB: "",
          paths: [],
          description: "hub conflict",
        },
        {
          ownerA: "",
          branchA: "",
          baseA: "",
          ownerB: "",
          branchB: "",
          baseB: "",
          paths: [],
          description: "",
        },
      ],
      deadLetters: [{ target: "", count: 1, lastSender: "", lastTs: null }],
      claims: [
        {
          claim: {
            task_id: "",
            owner: "",
            lease_expires_at: null,
            paths: [],
            stale: true,
            git: null,
          },
          urgency: "stale",
          inConflict: false,
          secondsToExpiry: null,
        },
      ],
      board: [
        {
          taskId: "",
          title: "",
          status: "blocked",
          bucket: "blocked",
          dependsOn: [],
          unblocks: [],
        },
      ],
      approvals: [{ action: "", namespace: "", taskId: "", requester: "" }],
      waits: [{ taskId: "", title: "", who: "", onWhat: [], since: null, status: "" }],
    });
    expect(items.every((item) => item.action === null)).toBe(true);
    expect(items.map((item) => item.evidence)).toContain("hub status: blocked");
    expect(items.map((item) => item.evidence)).toContain("0 overlapping paths");
    expect(items.map((item) => item.evidence)).toContain("unowned waits on unrecorded dependency");
    expect(items.map((item) => item.subject)).toContain("unnamed claim");
    expect(items.map((item) => item.evidence)).toContain("stale hold by unknown owner");
  });

  it("states singular and plural receipt evidence without inventing timestamps", () => {
    const items = deriveAttentionQueue({
      ...EMPTY,
      communication: {
        nodes: [],
        projects: [],
        messages: 3,
        edges: [
          {
            id: "failed-one",
            source: "a",
            target: "b",
            messages: 1,
            delivered: 0,
            deferred: 0,
            failed: 1,
            lastTs: 0,
            health: "failed",
          },
          {
            id: "deferred-two",
            source: "b",
            target: "c",
            messages: 2,
            delivered: 0,
            deferred: 2,
            failed: 0,
            lastTs: 0,
            health: "deferred",
          },
        ],
      },
      approvals: [
        { action: "relay", namespace: "fleet", taskId: "", requester: "operator" },
      ],
    });
    expect(items[0]?.evidence).toBe("1 failed receipt across 1 retained message");
    expect(items[0]?.observedAt).toBeNull();
    expect(items[1]?.evidence).toBe("2 deferred receipts across 2 retained messages");
    expect(items[2]?.subject).toBe("fleet");
  });
});
