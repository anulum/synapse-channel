// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — compact log grouping: one row per task, its lifecycle inline

// Temporal's Compact mode groups a workflow's related events into one logical
// row; the cockpit analogue groups the log by task, so a claim → progress →
// release → done chain reads as one story instead of interleaved lines.
// Events naming no task (bus chatter, presence) stay ungrouped — chatter is
// not a lifecycle and pretending otherwise would invent structure.

import type { CockpitEvent } from "../types";

/** One task's lifecycle as observed in the log window. */
export interface TaskGroup {
  readonly taskId: string;
  /** The group's events, oldest first — a lifecycle reads forward. */
  readonly events: readonly CockpitEvent[];
  /** Timestamp of the group's newest event (drives group ordering). */
  readonly lastTs: number;
  /** The most recent actor seen on the task, "" when none was named. */
  readonly lastActor: string;
}

/** The compact projection: task groups plus the ungrouped remainder. */
export interface CompactLog {
  /** Groups ordered by most recent activity first. */
  readonly groups: readonly TaskGroup[];
  /** Events naming no task, newest first (chatter, presence). */
  readonly ungrouped: readonly CockpitEvent[];
}

/**
 * Group a newest-first event list by task. Within a group events run oldest
 * first (the lifecycle order); groups order by their newest event; events
 * without a task id stay flat. The input is never mutated.
 */
export function groupByTask(events: readonly CockpitEvent[]): CompactLog {
  const byTask = new Map<string, CockpitEvent[]>();
  const ungrouped: CockpitEvent[] = [];
  for (const event of events) {
    if (event.taskId === "") {
      ungrouped.push(event);
      continue;
    }
    const bucket = byTask.get(event.taskId);
    if (bucket === undefined) byTask.set(event.taskId, [event]);
    else bucket.push(event);
  }
  const groups = [...byTask.entries()].map(([taskId, grouped]): TaskGroup => {
    const oldestFirst = [...grouped].reverse();
    const newest = grouped[0] as CockpitEvent;
    const lastActor = grouped.find((event) => event.actor !== "")?.actor ?? "";
    return { taskId, events: oldestFirst, lastTs: newest.ts, lastActor };
  });
  groups.sort((a, b) => b.lastTs - a.lastTs || a.taskId.localeCompare(b.taskId));
  return { groups, ungrouped };
}
