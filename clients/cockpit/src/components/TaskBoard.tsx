// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the task board: the shared plan with hub-verdicted dependency edges

import type { JSX } from "react";
import { memo, useState } from "react";

import type { BoardBucket, BoardTask, BoardTruncation, DependencyChip } from "../lib/board";
import { renderBoardReport } from "../lib/boardReport";
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
  /** Opens the task detail drawer. */
  readonly onInspect?: ((taskId: string) => void) | undefined;
}

function TaskRow({ task, onInspect }: TaskRowProps): JSX.Element {
  return (
    <li
      className={`board-row board-row--${task.bucket}${onInspect !== undefined ? " board-row--link" : ""}`}
      onClick={onInspect === undefined ? undefined : () => onInspect(task.taskId)}
    >
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
  /** Opens the task detail drawer. */
  readonly onInspect?: ((taskId: string) => void) | undefined;
  /** The active focus lens ("" = off), stated in the head. */
  readonly lens?: string;
}

function TaskBoardView({ tasks, connected, truncation, onInspect, lens = "" }: TaskBoardProps): JSX.Element {
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
  // Headroom against the hub's own applied cap — plain arithmetic on two
  // hub facts, no prediction. Loud from nine tenths full.
  const taskCap = truncation?.taskCap ?? null;
  const nearCap =
    taskCap !== null &&
    truncation?.totalTasks != null &&
    truncation.totalTasks >= Math.ceil(taskCap * 0.9);

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
        {lens !== "" && <span className="panel__sub panel__sub--warn">{`lens: ${lens}`}</span>}
        {constrained && (
          <span className="panel__sub">{`${shown.length} of ${tasks.length} shown`}</span>
        )}
        {blocked > 0 && <span className="panel__sub panel__sub--warn">{blocked} blocked</span>}
        {nearCap && truncation?.totalTasks != null && (
          <span
            className="panel__sub panel__sub--warn"
            title="The full board is within a tenth of the hub's applied task cap; new declarations will start trimming the served page."
          >
            {`near cap · ${truncation.totalTasks}/${taskCap}`}
          </span>
        )}
        {taskCap !== null && !nearCap && (
          <span className="panel__sub" title="The hub serves this board under an active task cap.">
            {`cap ${taskCap}`}
          </span>
        )}
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
        <button
          type="button"
          className="board-chip"
          disabled={shown.length === 0}
          onClick={() => {
            const scope = [
              lens === "" ? "" : `focus ${lens}`,
              constrained ? "board query applied" : "",
            ]
              .filter((part) => part !== "")
              .join(" · ");
            const report = renderBoardReport(shown, scope === "" ? "full board" : scope, new Date().toISOString());
            const blob = new Blob([report], { type: "text/markdown" });
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement("a");
            anchor.href = url;
            anchor.download = "board-report.md";
            anchor.click();
            URL.revokeObjectURL(url);
          }}
          title="Download the shown board as a Markdown report (scope stated inside)"
        >
          report
        </button>
      </div>
      <div className="panel__body" tabIndex={0}>
        {!connected ? (
          <p className="panel__placeholder">Waiting for the hub.</p>
        ) : tasks.length === 0 ? (
          <p className="panel__placeholder">The board is empty — no tasks declared.</p>
        ) : shown.length === 0 ? (
          <p className="panel__placeholder">No task matches the query.</p>
        ) : (
          <ul className="board">
            {active.map((task) => (
              <TaskRow key={task.taskId} task={task} onInspect={onInspect} />
            ))}
            {doneShown.map((task) => (
              <TaskRow key={task.taskId} task={task} onInspect={onInspect} />
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
