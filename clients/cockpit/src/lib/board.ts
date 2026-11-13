// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — shape the blackboard task graph and findings for the board panel

import type { FleetSnapshot, TaskGraphEdge } from "../types";

/**
 * A task's board bucket, worst-first. `blocked` = at least one prerequisite
 * unmet or missing; `ready` = the hub reports it runnable; `open` = declared,
 * neither blocked nor ready-flagged (typically claimed or in flight);
 * `done` = terminal.
 */
export type BoardBucket = "blocked" | "ready" | "open" | "done";

/** One prerequisite of a task, with the hub's satisfaction verdict. */
export interface DependencyChip {
  readonly taskId: string;
  readonly satisfied: boolean;
  /** True when the prerequisite is absent from the board entirely. */
  readonly missing: boolean;
  /** Prerequisite status as the hub reports it, or `missing`. */
  readonly status: string;
}

/** One blackboard task shaped for the board panel. */
export interface BoardTask {
  readonly taskId: string;
  readonly title: string;
  readonly status: string;
  readonly bucket: BoardBucket;
  /** Prerequisites gating this task, hub-verdicted. */
  readonly dependsOn: readonly DependencyChip[];
  /** Task ids this task gates; for a done task these are what it unblocked. */
  readonly unblocks: readonly string[];
}

const BUCKET_RANK: Record<BoardBucket, number> = {
  blocked: 0,
  ready: 1,
  open: 2,
  done: 3,
};

/** Statuses the hub's graph treats as terminal for dependency satisfaction. */
/** Closed as the ledger means it: done or cancelled (`TERMINAL_LEDGER_STATUSES`). */
function isDone(status: string): boolean {
  return status === "done" || status === "cancelled";
}

function bucketOf(status: string, ready: boolean, dependsOn: readonly DependencyChip[]): BoardBucket {
  if (isDone(status)) return "done";
  // A task is blocked because the board says so (operators mark tasks `blocked`
  // for reasons no edge carries) or, while still `open`, by an unmet edge. A task
  // in progress has started, so an unfinished prerequisite does not block it:
  // the rule of the hub's `tasks_blocked` headline counter.
  if (status === "blocked" || (status === "open" && dependsOn.some((chip) => !chip.satisfied))) {
    return "blocked";
  }
  if (ready) return "ready";
  return "open";
}

function chipOf(edge: TaskGraphEdge): DependencyChip {
  return {
    taskId: edge.from,
    satisfied: edge.satisfied,
    missing: edge.missing,
    status: edge.from_status,
  };
}

/**
 * Shape the hub's task dependency graph into board rows: every node with its
 * hub-verdicted prerequisite chips and the tasks it gates, ordered blocked →
 * ready → open → done, then by task id. The bucket verdict reuses the graph's
 * own edge satisfaction — the panel never re-derives what the hub decided.
 */
export function deriveBoard(snapshot: FleetSnapshot | null): BoardTask[] {
  if (snapshot === null) return [];
  const graph = snapshot.fleet.task_graph;
  const byDependent = new Map<string, TaskGraphEdge[]>();
  const byPrerequisite = new Map<string, string[]>();
  for (const edge of graph.edges) {
    const gates = byDependent.get(edge.to);
    if (gates === undefined) byDependent.set(edge.to, [edge]);
    else gates.push(edge);
    const dependents = byPrerequisite.get(edge.from);
    if (dependents === undefined) byPrerequisite.set(edge.from, [edge.to]);
    else dependents.push(edge.to);
  }

  const rows = graph.nodes.map((node): BoardTask => {
    const dependsOn = (byDependent.get(node.task_id) ?? []).map(chipOf);
    return {
      taskId: node.task_id,
      title: node.title,
      status: node.status,
      bucket: bucketOf(node.status, node.ready, dependsOn),
      dependsOn,
      unblocks: byPrerequisite.get(node.task_id) ?? [],
    };
  });
  rows.sort(
    (a, b) => BUCKET_RANK[a.bucket] - BUCKET_RANK[b.bucket] || a.taskId.localeCompare(b.taskId),
  );
  return rows;
}

/** The board's own truncation statement, present when a task cap is set. */
export interface BoardTruncation {
  /** Total tasks on the full board, or null when the hub sent no cap signal. */
  readonly totalTasks: number | null;
  /** Whether the served task list is a capped subset of the full board. */
  readonly truncated: boolean;
  /** The applied board cap, or null when the board is served uncapped. */
  readonly taskCap: number | null;
  /** Closed tasks the bounded snapshot counted but did not send. */
  readonly closedOmitted: number;
  /** The headline's `tasks_blocked` (cannot start), or null without a headline. */
  readonly blocked: number | null;
}

/**
 * Read the hub's board-cap signal (`total_tasks` + `truncated`, sent when
 * `--board-task-cap` trims the reply) so the panel can state "N of M" instead
 * of presenting a capped list as the whole plan, and the dashboard's own count
 * of the closed tasks its bounded snapshot left out.
 */
export function boardTruncation(snapshot: FleetSnapshot | null): BoardTruncation {
  if (snapshot === null) {
    return { totalTasks: null, truncated: false, taskCap: null, closedOmitted: 0, blocked: null };
  }
  const total = snapshot.board["total_tasks"];
  const cap = snapshot.board["task_cap"];
  return {
    totalTasks: typeof total === "number" && Number.isFinite(total) ? Math.trunc(total) : null,
    truncated: snapshot.board["truncated"] === true,
    // Present only while a cap is ACTIVE (the hub omits it uncapped).
    taskCap: typeof cap === "number" && Number.isFinite(cap) && cap > 0 ? Math.trunc(cap) : null,
    closedOmitted: snapshot.bounds === null ? 0 : snapshot.bounds.closedOmitted,
    blocked: snapshot.headline === null ? null : snapshot.headline.tasksBlocked,
  };
}
