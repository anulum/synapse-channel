// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — toast deltas: say it once when the state turns worse (or better)

// The panels show state; a toast marks a TRANSITION the operator should not
// have to spot by diffing two glances: a task newly blocked, a new advisory
// conflict, a dead letter appearing or deepening, the risk rail crossing
// from amber to red — and one good transition, a task newly done. Facts are
// captured per poll and the diff is arithmetic; the first capture emits
// nothing (booting into a busy fleet is not fifty transitions).

import type { BoardTask } from "./board";
import type { BranchConflictView } from "./claims";
import type { DeadLetterView } from "./deadLetters";
import type { RiskView } from "../types";

/** One transition worth saying. */
export interface Toast {
  /** Stable identity for dismissal and deduping. */
  readonly id: string;
  readonly severity: "ok" | "warn" | "crit";
  readonly text: string;
}

/** The compared cut of a poll's state. */
export interface FleetFacts {
  readonly blocked: ReadonlySet<string>;
  readonly done: ReadonlySet<string>;
  readonly conflicts: ReadonlySet<string>;
  /** Dead-letter unread count per target. */
  readonly deadLetters: ReadonlyMap<string, number>;
  readonly riskRed: boolean;
  /** The hub's config-posture fingerprint; "" when the hub predates it. */
  readonly configEpoch: string;
}

/** Capture the compared facts from live (never reconstructed) data. */
export function factsOf(
  board: readonly BoardTask[],
  conflicts: readonly BranchConflictView[],
  deadLetters: readonly DeadLetterView[],
  risk: RiskView | null,
  configEpoch = "",
): FleetFacts {
  return {
    configEpoch,
    blocked: new Set(board.filter((task) => task.bucket === "blocked").map((task) => task.taskId)),
    done: new Set(board.filter((task) => task.bucket === "done").map((task) => task.taskId)),
    conflicts: new Set(
      conflicts.map((conflict) => [conflict.ownerA, conflict.ownerB].sort().join(" vs ")),
    ),
    deadLetters: new Map(deadLetters.map((letter) => [letter.target, letter.count])),
    riskRed: risk !== null && risk.signals.some((signal) => signal.level === "red"),
  };
}

/**
 * The transitions between two captures, worst first. `previous` null (the
 * first capture) emits nothing.
 */
export function toastsBetween(previous: FleetFacts | null, next: FleetFacts): Toast[] {
  if (previous === null) return [];
  const toasts: Toast[] = [];

  for (const [target, count] of next.deadLetters) {
    const before = previous.deadLetters.get(target) ?? 0;
    if (count > before) {
      toasts.push({
        id: `dead:${target}:${count}`,
        severity: "crit",
        text: `dead letter: ${count} unread for ${target} — nobody listening`,
      });
    }
  }
  for (const key of next.conflicts) {
    if (!previous.conflicts.has(key)) {
      toasts.push({ id: `conflict:${key}`, severity: "crit", text: `branch conflict: ${key}` });
    }
  }
  if (next.riskRed && !previous.riskRed) {
    toasts.push({ id: "risk:red", severity: "crit", text: "risk rail crossed amber → red" });
  }
  if (previous.configEpoch !== "" && next.configEpoch !== "" && previous.configEpoch !== next.configEpoch) {
    toasts.push({
      id: `epoch:${next.configEpoch}`,
      severity: "crit",
      text: `hub config epoch changed: ${previous.configEpoch.slice(0, 8)} → ${next.configEpoch.slice(0, 8)}`,
    });
  }
  for (const taskId of next.blocked) {
    if (!previous.blocked.has(taskId)) {
      toasts.push({ id: `blocked:${taskId}`, severity: "warn", text: `task blocked: ${taskId}` });
    }
  }
  for (const taskId of next.done) {
    if (!previous.done.has(taskId)) {
      toasts.push({ id: `done:${taskId}`, severity: "ok", text: `task done: ${taskId}` });
    }
  }

  const rank: Record<Toast["severity"], number> = { crit: 0, warn: 1, ok: 2 };
  toasts.sort((a, b) => rank[a.severity] - rank[b.severity] || a.id.localeCompare(b.id));
  return toasts;
}
