// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the task board: the shared plan with hub-verdicted dependency edges

import { memo, useState } from "react";

import type { BoardBucket, BoardTask, BoardTruncation, DependencyChip } from "../lib/board";
import {
  boardQueryConstrained,
  bucketCounts,
  filterBoard,
  OPEN_BOARD_QUERY,
  toggleBucket,
  type BoardQuery,
} from "../lib/boardFilter";

/** Bucket chips in triage order. */
const BUCKET_ORDER: readonly BoardBucket[] = ["blocked", "ready", "open", "done"];

/** Glyph per bucket — redundant with colour and order, never colour alone. */
const BUCKET_GLYPH: Record<BoardBucket, string> = {
  blocked: "◼",
  ready: "▶",
  open: "○",
  done: "✓",
};

/** How many done tasks show before the tail collapses into a count. */
const DONE_SHOWN = 6;

function chipClass(chip: DependencyChip): string {
  if (chip.missing) return "dep-chip dep-chip--missing";
  if (chip.satisfied) return "dep-chip dep-chip--satisfied";
  return "dep-chip dep-chip--waiting";
}

function chipTitle(chip: DependencyChip): string {
  if (chip.missing) return `${chip.taskId} is not on the board`;
  return `${chip.taskId}: ${chip.status}`;
}

interface TaskRowProps {
  readonly task: BoardTask;
}

function TaskRow({ task }: TaskRowProps): JSX.Element {
  return (
    <li className={`board-row board-row--${task.bucket}`}>
      <span className="board-row__glyph" aria-hidden="true">
        {BUCKET_GLYPH[task.bucket]}
      </span>
      <span className="board-row__id">
        <span className="board-row__task" title={task.taskId}>
          {task.taskId}
        </span>
        {task.title !== "" && (
          <span className="board-row__title" title={task.title}>
            {task.title}
          </span>
        )}
      </span>
      <span className="board-row__status">{task.status}</span>
      {(task.dependsOn.length > 0 || (task.bucket === "done" && task.unblocks.length > 0)) && (
        <span className="board-row__edges">
          {task.dependsOn.map((chip) => (
            <span key={chip.taskId} className={chipClass(chip)} title={chipTitle(chip)}>
              {chip.missing ? "✕" : chip.satisfied ? "✓" : "…"} {chip.taskId}
            </span>
          ))}
          {task.bucket === "done" &&
            task.unblocks.map((dependent) => (
              <span
                key={dependent}
                className="dep-chip dep-chip--unblocks"
                title={`${task.taskId} gates ${dependent}; done, so ${dependent} is no longer waiting on it`}
              >
                ↳ {dependent}
              </span>
            ))}
        </span>
      )}
    </li>
  );
}

interface TaskBoardProps {
  /** Board rows, already bucket-ranked by the board lib. */
  readonly tasks: readonly BoardTask[];
  /** Whether a snapshot has arrived at all (drives the honest empty state). */
  readonly connected: boolean;
  /** The hub's own board-cap statement; drives the "N of M" count. */
  readonly truncation?: BoardTruncation | undefined;
}

function TaskBoardView({ tasks, connected, truncation }: TaskBoardProps): JSX.Element {
  // The board query is panel-local: a fifty-task board needs finding, not
  // sharing, so it does not ride the URL the way the log's query does.
  const [query, setQuery] = useState<BoardQuery>(OPEN_BOARD_QUERY);
  const constrained = boardQueryConstrained(query);
  const counts = bucketCounts(tasks);
  const shown = filterBoard(tasks, query);
  const done = shown.filter((task) => task.bucket === "done");
  const active = shown.filter((task) => task.bucket !== "done");
  // A constrained board is being searched: show every match, no done-cap.
  const doneShown = constrained ? done : done.slice(0, DONE_SHOWN);
  const doneOverflow = done.length - doneShown.length;
  const blocked = active.filter((task) => task.bucket === "blocked").length;
  const capped = truncation?.truncated === true && truncation.totalTasks !== null;

  return (
    <section className="panel" aria-label="Task board">
      <div className="panel__head">
        <span>Board</span>
        <span
          className="panel__count"
          title={capped ? "The hub capped this reply; the full board is larger." : undefined}
        >
          {capped ? `${tasks.length} of ${truncation.totalTasks}` : tasks.length}
        </span>
        {constrained && (
          <span className="panel__sub">{`${shown.length} of ${tasks.length} shown`}</span>
        )}
        {blocked > 0 && <span className="panel__sub panel__sub--warn">{blocked} blocked</span>}
        {capped && blocked === 0 && !constrained && <span className="panel__sub">capped reply</span>}
      </div>
      <div className="board-controls">
        <input
          className="log-controls__search"
          value={query.text}
          onChange={(change) => setQuery({ ...query, text: change.target.value })}
          placeholder="find a task (id, title)"
          aria-label="Find a task by id or title"
        />
        {BUCKET_ORDER.map((bucket) => (
          <button
            key={bucket}
            type="button"
            className={`board-chip board-chip--${bucket}${
              query.buckets?.includes(bucket) === true ? " board-chip--active" : ""
            }`}
            onClick={() => setQuery(toggleBucket(query, bucket))}
            aria-pressed={query.buckets?.includes(bucket) === true}
            title={`Only ${bucket} tasks`}
          >
            {`${bucket} ${counts[bucket]}`}
          </button>
        ))}
        {constrained && (
          <button
            type="button"
            className="panel__clear"
            onClick={() => setQuery(OPEN_BOARD_QUERY)}
            title="Clear the board query"
          >
            reset
          </button>
        )}
      </div>
      <div className="panel__body">
        {!connected ? (
          <p className="panel__placeholder">Waiting for the hub.</p>
        ) : tasks.length === 0 ? (
          <p className="panel__placeholder">The board is empty — no tasks declared.</p>
        ) : shown.length === 0 ? (
          <p className="panel__placeholder">No task matches the query.</p>
        ) : (
          <ul className="board">
            {active.map((task) => (
              <TaskRow key={task.taskId} task={task} />
            ))}
            {doneShown.map((task) => (
              <TaskRow key={task.taskId} task={task} />
            ))}
            {doneOverflow > 0 && (
              <li className="board-row board-row--more">{`+${doneOverflow} more done`}</li>
            )}
          </ul>
        )}
      </div>
    </section>
  );
}

/** Memoised: re-renders only when its own data changes, not on the 1 s clock. */
export const TaskBoard = memo(TaskBoardView);
