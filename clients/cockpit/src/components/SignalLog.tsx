// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the signal log: the plain, legible table under the spine

import { memo, useRef, useState } from "react";

import { actorsInWindow, eventsInWindow, windowEdgeLabel, type TimeWindow } from "../lib/brush";
import { applyQuery, isConstrained, OPEN_QUERY, type LogQuery } from "../lib/logQuery";
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
  /** Jump to the causality inspector for the subject a row names. */
  readonly onSelectTask?: ((subject: string) => void) | undefined;
  /**
   * Where the events come from. `hub` = the durable log (real seq + ts; every
   * row can hop by its exact sequence); `derived` = snapshot-diff transitions
   * (local seq, poll-quantised ts; only task-naming rows hop, by task id).
   */
  readonly provenance?: "hub" | "derived";
  /** The operator's query (text/kinds/order), owned by the caller. */
  readonly query?: LogQuery;
  /** Query updates (typing, order toggle, clear). */
  readonly onQueryChange?: ((query: LogQuery) => void) | undefined;
}

function SignalLogView({
  events,
  window = null,
  onClearWindow,
  onSelectTask,
  provenance = "derived",
  query = OPEN_QUERY,
  onQueryChange,
}: SignalLogProps): JSX.Element {
  // Pause freezes the VIEW while the feed keeps recording — the frozen list
  // is a real snapshot of what was on screen, and the header counts what has
  // arrived since, so nothing is silently missed.
  const [paused, setPaused] = useState(false);
  const frozen = useRef<readonly CockpitEvent[]>([]);
  const togglePause = (): void => {
    if (!paused) frozen.current = events;
    setPaused(!paused);
  };
  const base = paused ? frozen.current : events;
  let newerCount = 0;
  if (paused) {
    const frozenHead = frozen.current[0]?.seq;
    if (frozenHead === undefined) {
      newerCount = events.length;
    } else {
      const headAt = events.findIndex((event) => event.seq === frozenHead);
      newerCount = headAt === -1 ? events.length : headAt;
    }
  }

  const shown = applyQuery(eventsInWindow(base, window), query);
  const actors = window === null ? [] : actorsInWindow(base, window);

  return (
    <section className="panel" aria-label="Signal log">
      <div className="panel__head">
        <span>Signal log</span>
        <span className="panel__count">{shown.length}</span>
        {window === null ? (
          <span className="panel__sub">
            {provenance === "hub" ? "hub event log" : "observed transitions"}
          </span>
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
      <div className="log-controls">
        <input
          className="log-controls__search"
          value={query.text}
          onChange={(change) => onQueryChange?.({ ...query, text: change.target.value })}
          placeholder="search events (actor, task, text)"
          aria-label="Search events by actor, task, or text"
        />
        <button
          type="button"
          className="log-controls__toggle"
          onClick={() =>
            onQueryChange?.({ ...query, order: query.order === "newest" ? "oldest" : "newest" })
          }
          title="Toggle render order"
        >
          {query.order === "newest" ? "newest ↓" : "oldest ↑"}
        </button>
        <button
          type="button"
          className={`log-controls__toggle${paused ? " log-controls__toggle--paused" : ""}`}
          onClick={togglePause}
          aria-pressed={paused}
          title="Freeze the view while the feed keeps recording"
        >
          {paused ? `paused · ${newerCount} new` : "pause"}
        </button>
        {isConstrained(query) && (
          <button
            type="button"
            className="panel__clear"
            onClick={() => onQueryChange?.(OPEN_QUERY)}
            title="Clear the query"
          >
            reset
          </button>
        )}
      </div>
      <div className="panel__body panel__body--flush">
        {shown.length === 0 ? (
          <p className="panel__placeholder panel__placeholder--padded">
            {isConstrained(query)
              ? "No events match the query."
              : window === null
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
                    {onSelectTask !== undefined &&
                    ((provenance === "hub" && event.kind !== "chat") || event.taskId !== "") ? (
                      <button
                        type="button"
                        className="log__hop"
                        title={
                          provenance === "hub"
                            ? `Trace the recorded causes of event seq ${event.seq}`
                            : `Trace the recorded causes of ${event.taskId}`
                        }
                        onClick={() =>
                          onSelectTask(provenance === "hub" ? String(event.seq) : event.taskId)
                        }
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
