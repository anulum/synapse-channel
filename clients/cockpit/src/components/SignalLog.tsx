// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the signal log: the plain, legible table under the spine

import { memo } from "react";

import { actorsInWindow, eventsInWindow, windowEdgeLabel, type TimeWindow } from "../lib/brush";
import type { CockpitEvent } from "../types";

/** Wall-clock HH:MM:SS for a spine event's timestamp (epoch seconds). */
function timeOf(event: CockpitEvent): string {
  return new Date(event.ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

interface SignalLogProps {
  /** Derived transition events, newest first; the caller owns the cap. */
  readonly events: readonly CockpitEvent[];
  /** The brushed spine window filtering this log, or null for everything. */
  readonly window?: TimeWindow | null;
  /** Clears the brushed window (also clears the spine highlight). */
  readonly onClearWindow?: (() => void) | undefined;
  /** Jump to the causality inspector for the task a row names. */
  readonly onSelectTask?: ((taskId: string) => void) | undefined;
}

function SignalLogView({
  events,
  window = null,
  onClearWindow,
  onSelectTask,
}: SignalLogProps): JSX.Element {
  const shown = eventsInWindow(events, window);
  const actors = window === null ? [] : actorsInWindow(events, window);

  return (
    <section className="panel" aria-label="Signal log">
      <div className="panel__head">
        <span>Signal log</span>
        <span className="panel__count">{shown.length}</span>
        {window === null ? (
          <span className="panel__sub">observed transitions</span>
        ) : (
          <span className="panel__sub panel__sub--brush">
            {`${windowEdgeLabel(window.fromTs)}–${windowEdgeLabel(window.toTs)} · ${
              actors.length
            } actor${actors.length === 1 ? "" : "s"}`}
            <button type="button" className="panel__clear" onClick={() => onClearWindow?.()}>
              clear
            </button>
          </span>
        )}
      </div>
      <div className="panel__body panel__body--flush">
        {shown.length === 0 ? (
          <p className="panel__placeholder panel__placeholder--padded">
            {window === null
              ? "No coordination events observed yet. The spine baseline stays flat until the fleet moves."
              : "No observed events inside the brushed window."}
          </p>
        ) : (
          <table className="log">
            <thead>
              <tr>
                <th scope="col">time</th>
                <th scope="col">lane</th>
                <th scope="col">kind</th>
                <th scope="col">actor</th>
                <th scope="col">event</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((event) => (
                <tr key={event.seq} className={`log__row log__row--${event.kind}`}>
                  <td className="log__time">{timeOf(event)}</td>
                  <td className="log__lane">{event.lane}</td>
                  <td className="log__kind">
                    <span className="log__dot" aria-hidden="true" />
                    {event.kind}
                  </td>
                  <td className="log__actor" title={event.actor}>
                    {event.actor === "" ? "—" : event.actor}
                  </td>
                  <td className="log__label" title={event.label}>
                    {event.taskId !== "" && onSelectTask !== undefined ? (
                      <button
                        type="button"
                        className="log__hop"
                        title={`Trace the recorded causes of ${event.taskId}`}
                        onClick={() => onSelectTask(event.taskId)}
                      >
                        {event.label}
                      </button>
                    ) : (
                      event.label
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}

/** Memoised: re-renders only when its own data changes, not on the 1 s clock. */
export const SignalLog = memo(SignalLogView);
