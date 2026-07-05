// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the task board's query: find a card on a fifty-task board

// The board grew past what one screen scan covers, so it gets the same
// treatment the signal log got: a text query over what the operator actually
// remembers (the task id or a word of its title) and bucket chips for the
// lifecycle slice. Filtering hides nothing silently — the head shows
// shown-of-total whenever the query constrains.

import type { BoardBucket, BoardTask } from "./board";

/** The operator's board query. `buckets` null = every bucket. */
export interface BoardQuery {
  readonly text: string;
  readonly buckets: readonly BoardBucket[] | null;
}

/** The unconstrained query — the whole board. */
export const OPEN_BOARD_QUERY: BoardQuery = { text: "", buckets: null };

/** Whether the query would hide anything at all. */
export function boardQueryConstrained(query: BoardQuery): boolean {
  return query.text.trim() !== "" || query.buckets !== null;
}

/** Case-insensitive match over the task's id and title. */
export function matchesBoardQuery(task: BoardTask, query: BoardQuery): boolean {
  if (query.buckets !== null && !query.buckets.includes(task.bucket)) return false;
  const needle = query.text.trim().toLowerCase();
  if (needle === "") return true;
  return (
    task.taskId.toLowerCase().includes(needle) || task.title.toLowerCase().includes(needle)
  );
}

/** Apply the query, preserving the board's ranking. */
export function filterBoard(tasks: readonly BoardTask[], query: BoardQuery): BoardTask[] {
  return tasks.filter((task) => matchesBoardQuery(task, query));
}

/** Per-bucket counts over the UNFILTERED board, for the chips' labels. */
export function bucketCounts(tasks: readonly BoardTask[]): Record<BoardBucket, number> {
  const counts: Record<BoardBucket, number> = { blocked: 0, ready: 0, open: 0, done: 0 };
  for (const task of tasks) counts[task.bucket] += 1;
  return counts;
}

/** Toggle one bucket in the query: null⇄single, add/remove, empty→null. */
export function toggleBucket(query: BoardQuery, bucket: BoardBucket): BoardQuery {
  if (query.buckets === null) return { ...query, buckets: [bucket] };
  const next = query.buckets.includes(bucket)
    ? query.buckets.filter((entry) => entry !== bucket)
    : [...query.buckets, bucket];
  return { ...query, buckets: next.length === 0 ? null : next };
}
