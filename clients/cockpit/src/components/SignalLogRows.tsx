// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — bounded signal-log evidence presentation

import type { JSX } from "react";
import { Fragment, useState } from "react";

import { groupByTask } from "../lib/logGroups";
import type { LogQuery } from "../lib/logQuery";
import { fetchAndVerify, type VerifyResult } from "../lib/merkleVerify";
import { eventMatchesSelection } from "../lib/selection";
import type { CockpitSelection } from "../lib/workspace";
import type { CockpitEvent } from "../types";

/**
 * How many rows the table PAINTS. Measured, not guessed: ten thousand DOM
 * rows cost 9.45 s to paint and 81 MB of heap; a thousand paint instantly.
 */
export const RENDERED_ROWS_CAP = 1000;

/** Wall-clock HH:MM:SS for a spine event's timestamp (epoch seconds). */
function timeOf(event: CockpitEvent): string {
  return new Date(event.ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function verifyLabel(result: VerifyResult): string {
  if (result.kind === "verified") {
    return `✓ committed to root ${result.root.slice(0, 12)}… (recomputed in this browser)`;
  }
  if (result.kind === "mismatch") return "✗ proof did not reconstruct the claimed root";
  if (result.kind === "absent") return `not in the committed tree: ${result.note}`;
  if (result.kind === "unserved") return "proof surface not served (/merkle-proof.json)";
  return `verify failed: ${result.message}`;
}

interface SignalLogRowsProps {
  readonly events: readonly CockpitEvent[];
  readonly view: LogQuery["view"];
  /** Provenance governing trace navigation, retained from the live component. */
  readonly navigationProvenance: "hub" | "derived";
  /** Provenance of the evidence currently shown, governing proof verification. */
  readonly evidenceProvenance: "hub" | "derived";
  readonly onSelectTask?: ((subject: string) => void) | undefined;
  readonly onSelectEvent?: ((seq: number) => void) | undefined;
  readonly selection?: CockpitSelection | null;
}

/** Flat or task-compacted presentation of one already-filtered evidence set. */
export function SignalLogRows({
  events,
  view,
  navigationProvenance,
  evidenceProvenance,
  onSelectTask,
  onSelectEvent,
  selection = null,
}: SignalLogRowsProps): JSX.Element {
  const [expandedSeq, setExpandedSeq] = useState<number | null>(null);
  const [verdicts, setVerdicts] = useState<ReadonlyMap<number, string>>(new Map());

  const runVerify = (seq: number): void => {
    setVerdicts((current) => new Map(current).set(seq, "verifying…"));
    void fetchAndVerify(seq).then((result) => {
      setVerdicts((current) => new Map(current).set(seq, verifyLabel(result)));
    });
  };

  const visible = events.slice(0, RENDERED_ROWS_CAP);
  const truncated = Math.max(0, events.length - RENDERED_ROWS_CAP);
  if (view === "compact") {
    return (
      <CompactLogList
        events={visible}
        onSelectTask={onSelectTask}
        provenance={navigationProvenance}
        truncated={truncated}
        selection={selection}
      />
    );
  }

  return (
    <table className="log">
      <thead>
        <tr>
          <th scope="col"><span className="visually-hidden">raw event</span></th>
          <th scope="col">time</th>
          <th scope="col">lane</th>
          <th scope="col">kind</th>
          <th scope="col">actor</th>
          <th scope="col">event</th>
        </tr>
      </thead>
      <tbody>
        {visible.map((event) => {
          const matchesSelection = eventMatchesSelection(event, selection);
          return (
            <Fragment key={event.seq}>
              <tr
                className={`log__row log__row--${event.kind}${matchesSelection ? " context-match" : ""}`}
                aria-current={matchesSelection ? "true" : undefined}
              >
                <td className="log__raw">
                  {event.payload !== undefined && (
                    <button
                      type="button"
                      className="log__raw-toggle"
                      aria-expanded={expandedSeq === event.seq}
                      title="Show the hub's raw stored event"
                      onClick={() => setExpandedSeq(expandedSeq === event.seq ? null : event.seq)}
                    >
                      {"{}"}
                    </button>
                  )}
                  {navigationProvenance === "hub" && onSelectEvent !== undefined && (
                    <button
                      type="button"
                      className="log__select"
                      aria-pressed={selection?.kind === "event" && selection.seq === event.seq}
                      aria-label={`Select event sequence ${event.seq}`}
                      onClick={() => onSelectEvent(event.seq)}
                    >
                      #{event.seq}
                    </button>
                  )}
                </td>
                <td className="log__time">{timeOf(event)}</td>
                <td className="log__lane">{event.lane}</td>
                <td className="log__kind"><span className="log__dot" aria-hidden="true" />{event.kind}</td>
                <td className="log__actor" title={event.actor}>{event.actor === "" ? "—" : event.actor}</td>
                <td className="log__label" title={event.label}>
                  {onSelectTask !== undefined &&
                  ((navigationProvenance === "hub" && event.kind !== "chat") || event.taskId !== "") ? (
                    <button
                      type="button"
                      className="log__hop"
                      title={
                        navigationProvenance === "hub"
                          ? `Trace the recorded causes of event seq ${event.seq}`
                          : `Trace the recorded causes of ${event.taskId}`
                      }
                      onClick={() => onSelectTask(navigationProvenance === "hub" ? String(event.seq) : event.taskId)}
                    >
                      {event.label}
                    </button>
                  ) : event.label}
                </td>
              </tr>
              {expandedSeq === event.seq && event.payload !== undefined && (
                <tr className="log__detail">
                  <td colSpan={6}>
                    <pre className="log__json">
                      {JSON.stringify(
                        { seq: event.seq, ts: event.ts, kind: event.kind, payload: event.payload },
                        null,
                        2,
                      )}
                    </pre>
                    {evidenceProvenance === "hub" && (
                      <div className="log__verify">
                        <button
                          type="button"
                          className="log-controls__toggle"
                          onClick={() => runVerify(event.seq)}
                          title="Fetch the RFC 6962 inclusion proof and recompute the root client-side"
                        >
                          verify inclusion
                        </button>
                        {verdicts.has(event.seq) && (
                          <span className="log__verify-verdict">{verdicts.get(event.seq)}</span>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
        {truncated > 0 && (
          <tr className="log__row">
            <td colSpan={6} className="log__more">
              {`+${truncated} more match — narrow the query to see them`}
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

interface CompactLogListProps {
  readonly events: readonly CockpitEvent[];
  readonly onSelectTask?: ((subject: string) => void) | undefined;
  readonly provenance: "hub" | "derived";
  readonly truncated: number;
  readonly selection: CockpitSelection | null;
}

/** One row per task with its observed lifecycle inline; chatter stays flat. */
function CompactLogList({
  events,
  onSelectTask,
  provenance,
  truncated,
  selection,
}: CompactLogListProps): JSX.Element {
  const compact = groupByTask(events);
  return (
    <div className="log-compact">
      {compact.groups.map((group) => (
        <div
          key={group.taskId}
          className={`log-group${group.events.some((event) => eventMatchesSelection(event, selection)) ? " context-match" : ""}`}
        >
          <div className="log-group__head">
            {onSelectTask !== undefined ? (
              <button
                type="button"
                className="log__hop log-group__task"
                title={`Trace the recorded causes of ${group.taskId}`}
                onClick={() => onSelectTask(group.taskId)}
              >
                {group.taskId}
              </button>
            ) : <span className="log-group__task">{group.taskId}</span>}
            {group.lastActor !== "" && <span className="log-group__actor" title={group.lastActor}>{group.lastActor}</span>}
            <span className="log-group__time">{timeOf(group.events.at(-1) as CockpitEvent)}</span>
          </div>
          <div className="log-group__chain">
            {group.events.map((event) => (
              <span
                key={event.seq}
                className={`log-chip log__row--${event.kind}${eventMatchesSelection(event, selection) ? " context-match" : ""}`}
                title={`${timeOf(event)} · ${event.label}${provenance === "hub" ? ` · seq ${event.seq}` : ""}`}
              >
                <span className="log__dot" aria-hidden="true" />{event.kind}
              </span>
            ))}
          </div>
        </div>
      ))}
      {compact.ungrouped.length > 0 && (
        <div className="log-group log-group--chatter">
          <div className="log-group__head">
            <span className="log-group__task">chatter · {compact.ungrouped.length}</span>
          </div>
          <div className="log-group__chain">
            {compact.ungrouped.slice(0, 40).map((event) => (
              <span
                key={event.seq}
                className={`log-chip log__row--${event.kind}${eventMatchesSelection(event, selection) ? " context-match" : ""}`}
                title={`${timeOf(event)} · ${event.actor === "" ? "" : `${event.actor} · `}${event.label}`}
              >
                <span className="log__dot" aria-hidden="true" />{event.kind}
              </span>
            ))}
            {compact.ungrouped.length > 40 && (
              <span className="log-chip log-chip--more">{`+${compact.ungrouped.length - 40}`}</span>
            )}
          </div>
        </div>
      )}
      {truncated > 0 && (
        <div className="log__more">
          {`+${truncated} more match beyond the render cap — narrow the query to see them`}
        </div>
      )}
    </div>
  );
}
