// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the detail drawer: one name, everything the fleet knows

import { useEffect } from "react";

import type { AgentDetail, TaskDetail } from "../lib/detail";
import { COLOUR_OF } from "../lib/events";
import type { CockpitEvent } from "../types";

function timeOf(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function EventList({
  events,
  more,
}: {
  readonly events: readonly CockpitEvent[];
  readonly more: number;
}): JSX.Element {
  if (events.length === 0) {
    return <p className="panel__placeholder">Nothing from it in the observed window.</p>;
  }
  return (
    <ul className="drawer__events">
      {events.map((event) => (
        <li key={event.seq} className="drawer__event">
          <span className="drawer__event-time">{timeOf(event.ts)}</span>
          <span
            className="drawer__event-dot"
            style={{ background: COLOUR_OF[event.kind] }}
            aria-hidden="true"
          />
          <span className="drawer__event-label" title={`${event.kind} · ${event.label}`}>
            {event.label}
          </span>
        </li>
      ))}
      {more > 0 && <li className="drawer__event drawer__event--more">{`+${more} more in the window`}</li>}
    </ul>
  );
}

interface DetailDrawerProps {
  /** Exactly one of the two details is shown. */
  readonly agent?: AgentDetail | undefined;
  readonly task?: TaskDetail | undefined;
  readonly onClose: () => void;
  /** Filters the signal log to this subject's text. */
  readonly onFilterLog: (text: string) => void;
  /** Hops into the causality inspector for a task. */
  readonly onTrace?: ((taskId: string) => void) | undefined;
}

/**
 * A right-hand drawer over the deck. Read-only like everything else: its
 * body is a different cut of data already on screen, and its two actions
 * only steer other panels (filter the log, trace the causality). Escape or
 * the backdrop closes it.
 */
export function DetailDrawer({ agent, task, onClose, onFilterLog, onTrace }: DetailDrawerProps): JSX.Element | null {
  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (agent === undefined && task === undefined) return null;
  const subject = agent !== undefined ? agent.name : (task?.taskId ?? "");

  return (
    <div className="drawer-veil" onClick={onClose}>
      <aside
        className="drawer"
        aria-label={agent !== undefined ? `Agent ${subject}` : `Task ${subject}`}
        onClick={(click) => click.stopPropagation()}
      >
        <div className="drawer__head">
          <span className="drawer__title" title={subject}>
            {subject}
          </span>
          <button type="button" className="panel__clear" onClick={onClose} aria-label="Close the drawer">
            close
          </button>
        </div>

        {agent !== undefined && (
          <div className="drawer__body">
            <div className="drawer__facts">
              <span className={`drawer__fact${agent.entry?.online === true ? " drawer__fact--ok" : " drawer__fact--warn"}`}>
                {agent.entry === null ? "not in roster" : agent.entry.online ? "online" : "offline"}
              </span>
              {agent.entry !== null &&
                agent.entry.roles.map((role) => (
                  <span key={role} className="drawer__fact">{`role: ${role}`}</span>
                ))}
              {agent.entry !== null && agent.entry.wakerMissing === true && (
                <span className="drawer__fact drawer__fact--warn">waker missing</span>
              )}
              {agent.entry !== null && agent.entry.inConflict && (
                <span className="drawer__fact drawer__fact--crit">in conflict</span>
              )}
            </div>

            <span className="drawer__section">claims held · {agent.claims.length}</span>
            {agent.claims.length === 0 ? (
              <p className="panel__placeholder">Holds nothing right now.</p>
            ) : (
              <ul className="drawer__list">
                {agent.claims.map((view) => (
                  <li key={view.claim.task_id} className="drawer__item">
                    <span className="drawer__item-id">{view.claim.task_id}</span>
                    <span className="drawer__item-note">
                      {view.claim.stale ? "stale" : view.urgency}
                      {view.claim.paths.length > 0 ? ` · ${view.claim.paths.join(", ")}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}

            {agent.deadLetters.length > 0 && (
              <>
                <span className="drawer__section drawer__section--crit">
                  unread mailbox · {agent.deadLetters.reduce((sum, letter) => sum + letter.count, 0)}
                </span>
                <ul className="drawer__list">
                  {agent.deadLetters.map((letter) => (
                    <li key={letter.target} className="drawer__item">
                      <span className="drawer__item-note">{`${letter.count} unread · last from ${letter.lastSender === "" ? "—" : letter.lastSender}`}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}

            <span className="drawer__section">recent activity</span>
            <EventList events={agent.recentEvents} more={agent.moreEvents} />
          </div>
        )}

        {task !== undefined && (
          <div className="drawer__body">
            <div className="drawer__facts">
              <span
                className={`drawer__fact${
                  task.task?.bucket === "blocked"
                    ? " drawer__fact--crit"
                    : task.task?.bucket === "done" || task.task?.bucket === "ready"
                      ? " drawer__fact--ok"
                      : ""
                }`}
              >
                {task.task === null
                  ? "not on the board"
                  : task.task.bucket === task.task.status
                    ? task.task.bucket
                    : `${task.task.bucket} · ${task.task.status}`}
              </span>
              {task.claim !== null && (
                <span className="drawer__fact">{`held by ${task.claim.claim.owner}`}</span>
              )}
            </div>
            {task.task !== null && task.task.title !== "" && (
              <p className="drawer__note">{task.task.title}</p>
            )}

            {task.task !== null && task.task.dependsOn.length > 0 && (
              <>
                <span className="drawer__section">depends on</span>
                <ul className="drawer__list">
                  {task.task.dependsOn.map((chip) => (
                    <li key={chip.taskId} className="drawer__item">
                      <span className="drawer__item-id">{chip.taskId}</span>
                      <span className="drawer__item-note">
                        {chip.missing ? "missing" : chip.satisfied ? "satisfied" : "waiting"}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}
            {task.task !== null && task.task.unblocks.length > 0 && (
              <>
                <span className="drawer__section">unblocks</span>
                <p className="drawer__note">{task.task.unblocks.join(", ")}</p>
              </>
            )}

            <span className="drawer__section">history in the window</span>
            <EventList events={task.recentEvents} more={task.moreEvents} />
          </div>
        )}

        <div className="drawer__actions">
          <button type="button" className="log-controls__toggle" onClick={() => onFilterLog(subject)}>
            filter log
          </button>
          {task !== undefined && onTrace !== undefined && (
            <button type="button" className="log-controls__toggle" onClick={() => onTrace(subject)}>
              trace causality
            </button>
          )}
        </div>
      </aside>
    </div>
  );
}
