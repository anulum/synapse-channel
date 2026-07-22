// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — retained event-window coverage strip

import type { JSX } from "react";

import type { EventCoverage } from "../lib/eventCoverage";

function sourceLabel(source: EventCoverage["source"]): string {
  if (source === "hub") return "hub event log";
  if (source === "derived") return "observed transitions";
  return "event source connecting";
}

function timeLabel(minTs: number, maxTs: number): string {
  const options: Intl.DateTimeFormatOptions = {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  };
  return `${new Date(minTs * 1000).toLocaleTimeString([], options)}–${new Date(
    maxTs * 1000,
  ).toLocaleTimeString([], options)}`;
}

export function DataCoverage({ coverage }: { readonly coverage: EventCoverage }): JSX.Element {
  const hasRange =
    coverage.minSeq !== null &&
    coverage.maxSeq !== null &&
    coverage.minTs !== null &&
    coverage.maxTs !== null;
  return (
    <aside className="event-coverage" aria-label="Event data coverage">
      <strong className="event-coverage__source">{sourceLabel(coverage.source)}</strong>
      <span>
        {coverage.retained} retained / {coverage.capacity} client cap
      </span>
      {hasRange && (
        <>
          <span>
            seq {coverage.minSeq}–{coverage.maxSeq}
          </span>
          <span>{timeLabel(coverage.minTs, coverage.maxTs)}</span>
        </>
      )}
      <span
        className={
          coverage.atCapacity
            ? "event-coverage__limit event-coverage__limit--full"
            : "event-coverage__limit"
        }
      >
        {coverage.atCapacity ? "retained window at cap" : "bounded client window"}
      </span>
    </aside>
  );
}
