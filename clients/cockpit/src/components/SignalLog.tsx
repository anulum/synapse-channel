// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the signal log: the plain, legible table under the spine

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
}

export function SignalLog({ events }: SignalLogProps): JSX.Element {
  return (
    <section className="panel" aria-label="Signal log">
      <div className="panel__head">
        <span>Signal log</span>
        <span className="panel__count">{events.length}</span>
        <span className="panel__sub">observed transitions</span>
      </div>
      <div className="panel__body panel__body--flush">
        {events.length === 0 ? (
          <p className="panel__placeholder panel__placeholder--padded">
            No coordination events observed yet. The spine baseline stays flat
            until the fleet moves.
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
              {events.map((event) => (
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
                    {event.label}
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
