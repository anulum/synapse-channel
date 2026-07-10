// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — toast delta tests

import { describe, expect, it } from "vitest";
import type { BoardTask } from "../src/lib/board";
import type { BranchConflictView } from "../src/lib/claims";
import type { DeadLetterView } from "../src/lib/deadLetters";
import { factsOf, toastsBetween } from "../src/lib/toasts";
import type { RiskView } from "../src/types";

function task(taskId: string, bucket: BoardTask["bucket"]): BoardTask {
  return { taskId, title: "", status: bucket, bucket, dependsOn: [], unblocks: [] };
}

function conflict(a: string, b: string): BranchConflictView {
  return { ownerA: a, branchA: "x", baseA: "main", ownerB: b, branchB: "y", baseB: "main", paths: [], description: "" };
}

function letter(target: string, count: number): DeadLetterView {
  return { target, count, lastSender: "s", lastTs: 1 };
}

function risk(level: "red" | "amber"): RiskView {
  return {
    level,
    signals: [{ level, category: "c", subject: "s", detail: "" }],
    safe_next_work: [],
  };
}

describe("factsOf", () => {
  it("captures the compared cut", () => {
    const facts = factsOf(
      [task("a", "blocked"), task("b", "done"), task("c", "open")],
      [conflict("y", "x")],
      [letter("CEO", 2)],
      risk("red"),
    );
    expect([...facts.blocked]).toEqual(["a"]);
    expect([...facts.done]).toEqual(["b"]);
    expect([...facts.conflicts]).toEqual(["x vs y"]);
    expect(facts.deadLetters.get("CEO")).toBe(2);
    expect(facts.riskRed).toBe(true);
    expect(factsOf([], [], [], null).riskRed).toBe(false);
    expect(factsOf([], [], [], risk("amber")).riskRed).toBe(false);
  });
});

describe("toastsBetween", () => {
  const quiet = factsOf([], [], [], null);

  it("emits nothing on the first capture", () => {
    expect(toastsBetween(null, factsOf([task("a", "blocked")], [], [], risk("red")))).toEqual([]);
  });

  it("marks worsening transitions worst-first and the one good one", () => {
    const next = factsOf(
      [task("newly-blocked", "blocked"), task("newly-done", "done")],
      [conflict("a", "b")],
      [letter("CEO", 3)],
      risk("red"),
    );
    const toasts = toastsBetween(quiet, next);
    expect(toasts.map((toast) => toast.severity)).toEqual(["crit", "crit", "crit", "warn", "ok"]);
    expect(toasts.map((toast) => toast.id)).toEqual([
      "conflict:a vs b",
      "dead:CEO:3",
      "risk:red",
      "blocked:newly-blocked",
      "done:newly-done",
    ]);
    expect(toasts[1]?.text).toContain("nobody listening");
  });

  it("stays silent on unchanged state and on improvements other than done", () => {
    const busy = factsOf([task("a", "blocked")], [conflict("a", "b")], [letter("t", 2)], risk("red"));
    expect(toastsBetween(busy, busy)).toEqual([]);
    // Everything clearing: no toasts (panels already show the calm).
    expect(toastsBetween(busy, quiet)).toEqual([]);
  });

  it("fires on a deepening dead-letter count but not on a draining one", () => {
    const two = factsOf([], [], [letter("t", 2)], null);
    const three = factsOf([], [], [letter("t", 3)], null);
    expect(toastsBetween(two, three)).toHaveLength(1);
    expect(toastsBetween(three, two)).toEqual([]);
  });

  it("does not re-announce a task that was already done", () => {
    // The task is done in both cuts, so the done toast fired earlier and must
    // not repeat — the already-done branch of the done scan.
    const before = factsOf([task("shipped", "done")], [], [], null);
    const after = factsOf([task("shipped", "done")], [], [], null);
    expect(toastsBetween(before, after)).toEqual([]);
  });
});
