// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — live cockpit transition-toast lifecycle tests

import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useCockpitToasts } from "../../src/hooks/useCockpitToasts";
import type { BoardTask } from "../../src/lib/board";
import type { BranchConflictView } from "../../src/lib/claims";
import type { DeadLetterView } from "../../src/lib/deadLetters";
import type { FleetSnapshot } from "../../src/types";

function snapshot(epoch: string, risk: "green" | "red" = "green"): FleetSnapshot {
  return {
    online_agents: [],
    agent_roles: {},
    hub_version: "",
    config_epoch: epoch,
    state: {},
    board: {},
    manifest: [],
    fleet: {
      agents: { live: [], waiters: [], missing_waiters: [] },
      claims: { active: 0, stale: 0, active_claims: [], stale_claims: [] },
      branch_conflicts: [],
      task_graph: { nodes: [], edges: [] },
      receipts: [],
    },
    risk: {
      level: risk,
      signals: risk === "red"
        ? [{ level: "red", category: "test", subject: "fleet", detail: "crossed" }]
        : [],
      safe_next_work: [],
    },
  };
}

interface ToastProps {
  readonly blocked: boolean;
  readonly snapshot: FleetSnapshot | null;
  readonly board: readonly BoardTask[];
  readonly conflicts: readonly BranchConflictView[];
  readonly deadLetters: readonly DeadLetterView[];
}

afterEach(() => {
  vi.useRealTimers();
});

describe("useCockpitToasts", () => {
  it("emits each live transition once, supports dismissal, and expires it", () => {
    vi.useFakeTimers();
    const initial: ToastProps = {
      blocked: false,
      snapshot: snapshot("epoch-a"),
      board: [],
      conflicts: [],
      deadLetters: [],
    };
    const { result, rerender } = renderHook(
      (props: ToastProps) => useCockpitToasts(props),
      { initialProps: initial },
    );
    expect(result.current.toasts).toEqual([]);

    const blockedTask: BoardTask = {
      taskId: "TASK-1",
      title: "Blocked task",
      status: "blocked",
      bucket: "blocked",
      dependsOn: [],
      unblocks: [],
    };
    const conflict: BranchConflictView = {
      ownerA: "alpha",
      branchA: "main",
      baseA: "main",
      ownerB: "beta",
      branchB: "main",
      baseB: "main",
      paths: ["src/a.ts"],
      description: "overlap",
    };
    rerender({
      blocked: false,
      snapshot: snapshot("epoch-b", "red"),
      board: [blockedTask],
      conflicts: [conflict],
      deadLetters: [{ target: "ghost", count: 1, lastSender: "alpha", lastTs: 10 }],
    });
    expect(result.current.toasts.map((toast) => toast.id)).toEqual([
      "conflict:alpha vs beta",
      "dead:ghost:1",
      "epoch:epoch-b",
      "risk:red",
      "blocked:TASK-1",
    ]);
    rerender({
      blocked: false,
      snapshot: snapshot("epoch-b", "red"),
      board: [blockedTask],
      conflicts: [conflict],
      deadLetters: [{ target: "ghost", count: 2, lastSender: "alpha", lastTs: 11 }],
    });
    expect(result.current.toasts).toHaveLength(6);
    rerender({
      blocked: false,
      snapshot: snapshot("epoch-b", "red"),
      board: [blockedTask],
      conflicts: [conflict],
      deadLetters: [{ target: "ghost", count: 2, lastSender: "alpha", lastTs: 11 }],
    });
    expect(result.current.toasts).toHaveLength(6);

    act(() => result.current.dismiss("dead:ghost:1"));
    expect(result.current.toasts.some((toast) => toast.id === "dead:ghost:1")).toBe(false);
    act(() => vi.advanceTimersByTime(8_000));
    expect(result.current.toasts).toEqual([]);

    rerender({
      blocked: true,
      snapshot: snapshot("epoch-b", "red"),
      board: [blockedTask],
      conflicts: [conflict],
      deadLetters: [],
    });
    expect(result.current.toasts).toEqual([]);
  });

  it("clears a pending transition timer on lock and unmount", () => {
    vi.useFakeTimers();
    const { result, rerender, unmount } = renderHook(
      (props: ToastProps) => useCockpitToasts(props),
      {
        initialProps: {
          blocked: false,
          snapshot: null,
          board: [],
          conflicts: [],
          deadLetters: [],
        } as ToastProps,
      },
    );
    rerender({
      blocked: false,
      snapshot: snapshot("epoch-a"),
      board: [],
      conflicts: [],
      deadLetters: [],
    });
    rerender({
      blocked: false,
      snapshot: snapshot("epoch-a"),
      board: [],
      conflicts: [],
      deadLetters: [{ target: "ghost", count: 1, lastSender: "alpha", lastTs: 10 }],
    });
    expect(result.current.toasts).toHaveLength(1);
    expect(vi.getTimerCount()).toBe(1);
    rerender({
      blocked: true,
      snapshot: snapshot("epoch-a"),
      board: [],
      conflicts: [],
      deadLetters: [],
    });
    expect(result.current.toasts).toEqual([]);
    expect(vi.getTimerCount()).toBe(0);

    rerender({
      blocked: false,
      snapshot: snapshot("epoch-a"),
      board: [],
      conflicts: [],
      deadLetters: [],
    });
    rerender({
      blocked: false,
      snapshot: snapshot("epoch-a"),
      board: [],
      conflicts: [],
      deadLetters: [{ target: "ghost", count: 2, lastSender: "alpha", lastTs: 11 }],
    });
    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
