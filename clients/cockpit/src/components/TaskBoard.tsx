// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the task board: the shared plan with hub-verdicted dependency edges

import type { BoardBucket, BoardTask, DependencyChip } from "../lib/board";

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
}

export function TaskBoard({ tasks, connected }: TaskBoardProps): JSX.Element {
  const done = tasks.filter((task) => task.bucket === "done");
  const active = tasks.filter((task) => task.bucket !== "done");
  const doneShown = done.slice(0, DONE_SHOWN);
  const doneOverflow = done.length - doneShown.length;
  const blocked = active.filter((task) => task.bucket === "blocked").length;

  return (
    <section className="panel" aria-label="Task board">
      <div className="panel__head">
        <span>Board</span>
        <span className="panel__count">{tasks.length}</span>
        {blocked > 0 && <span className="panel__sub panel__sub--warn">{blocked} blocked</span>}
      </div>
      <div className="panel__body">
        {!connected ? (
          <p className="panel__placeholder">Waiting for the hub.</p>
        ) : tasks.length === 0 ? (
          <p className="panel__placeholder">The board is empty — no tasks declared.</p>
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
