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
function isDone(status: string): boolean {
  return status === "done";
}

function bucketOf(status: string, ready: boolean, dependsOn: readonly DependencyChip[]): BoardBucket {
  if (isDone(status)) return "done";
  // A task is blocked either by an unmet edge or because the board itself says
  // so — operators mark tasks `blocked` for reasons no dependency edge carries.
  if (status === "blocked" || dependsOn.some((chip) => !chip.satisfied)) return "blocked";
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
}

/**
 * Read the hub's board-cap signal (`total_tasks` + `truncated`, sent when
 * `--board-task-cap` trims the reply) so the panel can state "N of M" instead
 * of presenting a capped list as the whole plan.
 */
export function boardTruncation(snapshot: FleetSnapshot | null): BoardTruncation {
  if (snapshot === null) return { totalTasks: null, truncated: false, taskCap: null };
  const total = snapshot.board["total_tasks"];
  const cap = snapshot.board["task_cap"];
  return {
    totalTasks: typeof total === "number" && Number.isFinite(total) ? Math.trunc(total) : null,
    truncated: snapshot.board["truncated"] === true,
    // Present only while a cap is ACTIVE (the hub omits it uncapped).
    taskCap: typeof cap === "number" && Number.isFinite(cap) && cap > 0 ? Math.trunc(cap) : null,
  };
}

/** One finding note from the blackboard progress ledger. */
export interface FindingNote {
  readonly author: string;
  readonly taskId: string;
  readonly text: string;
  /** Epoch seconds the note was posted, or null when the hub omitted it. */
  readonly postedAt: number | null;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asEpochOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Extract `kind == "finding"` notes from the board's progress ledger, newest
 * first. Only findings qualify — routine notes and release receipts stay in
 * their own surfaces.
 */
export function deriveFindings(snapshot: FleetSnapshot | null): FindingNote[] {
  if (snapshot === null) return [];
  const progress = snapshot.board["progress"];
  if (!Array.isArray(progress)) return [];
  const findings: FindingNote[] = [];
  for (const raw of progress) {
    if (typeof raw !== "object" || raw === null || Array.isArray(raw)) continue;
    const note = raw as Record<string, unknown>;
    if (asString(note["kind"]) !== "finding") continue;
    findings.push({
      author: asString(note["author"]),
      taskId: asString(note["task_id"]),
      text: asString(note["text"]),
      postedAt: asEpochOrNull(note["posted_at"]),
    });
  }
  findings.reverse();
  return findings;
}
