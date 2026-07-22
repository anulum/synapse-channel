// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — deterministic claim and task replay comparison

import type { BoardTask } from "./board";
import type { ClaimView } from "./claims";
import type { FleetStateAt } from "./stateAt";
import type { CockpitEvent } from "../types";

export type ReplayEntity = "claim" | "task";
export type ReplayChange = "added" | "removed" | "changed";

/** One state delta with an exact retained transition event when available. */
export interface ReplayDelta {
  readonly entity: ReplayEntity;
  readonly subject: string;
  readonly change: ReplayChange;
  readonly summary: string;
  readonly eventSeq: number | null;
}

/** A-to-B comparison of the two reconstructed fleet moments. */
export interface ReplayDiff {
  readonly fromSeq: number;
  readonly toSeq: number;
  readonly deltas: readonly ReplayDelta[];
  readonly added: number;
  readonly removed: number;
  readonly changed: number;
  readonly evidenced: number;
}

function sorted(values: readonly string[]): readonly string[] {
  return [...values].sort((left, right) => left.localeCompare(right));
}

function claimShape(view: ClaimView): string {
  const claim = view.claim;
  return JSON.stringify({
    owner: claim.owner,
    urgency: view.urgency,
    stale: claim.stale,
    paths: sorted(claim.paths),
    leaseExpiresAt: claim.lease_expires_at,
    git: claim.git,
  });
}

function taskShape(task: BoardTask): string {
  return JSON.stringify({
    title: task.title,
    status: task.status,
    bucket: task.bucket,
    dependsOn: [...task.dependsOn]
      .map((dependency) => ({
        taskId: dependency.taskId,
        status: dependency.status,
        satisfied: dependency.satisfied,
        missing: dependency.missing,
      }))
      .sort((left, right) => left.taskId.localeCompare(right.taskId)),
    unblocks: sorted(task.unblocks),
  });
}

function claimChange(before: ClaimView, after: ClaimView): string {
  const changes: string[] = [];
  if (before.claim.owner !== after.claim.owner) {
    changes.push(`owner ${before.claim.owner || "—"} → ${after.claim.owner || "—"}`);
  }
  if (before.urgency !== after.urgency) changes.push(`state ${before.urgency} → ${after.urgency}`);
  if (JSON.stringify(sorted(before.claim.paths)) !== JSON.stringify(sorted(after.claim.paths))) {
    changes.push("path scope changed");
  }
  if (before.claim.lease_expires_at !== after.claim.lease_expires_at) changes.push("lease changed");
  if (JSON.stringify(before.claim.git) !== JSON.stringify(after.claim.git)) changes.push("git binding changed");
  return changes.join(" · ") || "claim evidence changed";
}

function taskChange(before: BoardTask, after: BoardTask): string {
  const changes: string[] = [];
  if (before.status !== after.status) changes.push(`status ${before.status || "—"} → ${after.status || "—"}`);
  if (before.bucket !== after.bucket) changes.push(`bucket ${before.bucket} → ${after.bucket}`);
  if (before.title !== after.title) changes.push("title changed");
  if (JSON.stringify(before.dependsOn) !== JSON.stringify(after.dependsOn)) changes.push("dependencies changed");
  if (JSON.stringify(sorted(before.unblocks)) !== JSON.stringify(sorted(after.unblocks))) {
    changes.push("dependants changed");
  }
  // collectionDeltas calls this only after taskShape proves a represented field changed.
  return changes.join(" · ");
}

function transitionEvent(
  events: readonly CockpitEvent[],
  entity: ReplayEntity,
  subject: string,
  aSeq: number,
  bSeq: number,
): number | null {
  const lower = Math.min(aSeq, bSeq);
  const upper = Math.max(aSeq, bSeq);
  const kinds = entity === "task" ? new Set(["task"]) : new Set(["claim", "lease", "release"]);
  let match: number | null = null;
  for (const event of events) {
    if (event.seq <= lower || event.seq > upper || event.taskId !== subject || !kinds.has(event.kind)) {
      continue;
    }
    if (match === null || event.seq > match) match = event.seq;
  }
  return match;
}

function collectionDeltas<T>(
  entity: ReplayEntity,
  before: readonly T[],
  after: readonly T[],
  identity: (value: T) => string,
  shape: (value: T) => string,
  describeChange: (left: T, right: T) => string,
  events: readonly CockpitEvent[],
  aSeq: number,
  bSeq: number,
): ReplayDelta[] {
  const beforeById = new Map(before.map((value) => [identity(value), value]));
  const afterById = new Map(after.map((value) => [identity(value), value]));
  const subjects = sorted([...new Set([...beforeById.keys(), ...afterById.keys()])]);
  const deltas: ReplayDelta[] = [];
  for (const subject of subjects) {
    const left = beforeById.get(subject);
    const right = afterById.get(subject);
    const eventSeq = transitionEvent(events, entity, subject, aSeq, bSeq);
    if (left === undefined && right !== undefined) {
      deltas.push({ entity, subject, change: "added", summary: `${entity} appears in B`, eventSeq });
    } else if (left !== undefined && right === undefined) {
      deltas.push({ entity, subject, change: "removed", summary: `${entity} absent from B`, eventSeq });
    } else if (left !== undefined && right !== undefined && shape(left) !== shape(right)) {
      deltas.push({
        entity,
        subject,
        change: "changed",
        summary: describeChange(left, right),
        eventSeq,
      });
    }
  }
  return deltas;
}

/** Compare two reconstructed moments without inferring unretained transition events. */
export function diffReplayStates(
  a: FleetStateAt,
  b: FleetStateAt,
  events: readonly CockpitEvent[],
): ReplayDiff {
  const deltas = [
    ...collectionDeltas(
      "claim",
      a.claims,
      b.claims,
      (view) => view.claim.task_id,
      claimShape,
      claimChange,
      events,
      a.asOfSeq,
      b.asOfSeq,
    ),
    ...collectionDeltas(
      "task",
      a.tasks,
      b.tasks,
      (task) => task.taskId,
      taskShape,
      taskChange,
      events,
      a.asOfSeq,
      b.asOfSeq,
    ),
  ];
  return {
    fromSeq: a.asOfSeq,
    toSeq: b.asOfSeq,
    deltas,
    added: deltas.filter((delta) => delta.change === "added").length,
    removed: deltas.filter((delta) => delta.change === "removed").length,
    changed: deltas.filter((delta) => delta.change === "changed").length,
    evidenced: deltas.filter((delta) => delta.eventSeq !== null).length,
  };
}
