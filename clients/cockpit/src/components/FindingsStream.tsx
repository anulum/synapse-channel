// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the findings stream: recorded discoveries, newest first

import type { JSX } from "react";
import { memo } from "react";

import type { FindingRow, FindingsState } from "../lib/findings";
import { Ident } from "./Ident";

/** Wall-clock HH:MM:SS for a finding's recorded time, or a dash without one. */
function timeOf(row: FindingRow): string {
  if (row.ts === null) return "—";
  return new Date(row.ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

/** The only freshness that says the producer re-checked the reference at its source. */
const VERIFIED_AT_SOURCE = "verified-at-source";

/** Lifecycles of a finding that no longer stands as current. */
const CLOSED_LIFECYCLES = new Set(["superseded", "retracted"]);

function freshnessTitle(freshness: string): string {
  return freshness === VERIFIED_AT_SOURCE
    ? "The producer re-checked the reference at its source in this session."
    : "Not re-checked at its source in this session.";
}

interface FindingsStreamProps {
  /** The findings feed: recorded findings, newest first, or why there are none. */
  readonly state: FindingsState;
}

function placeholderOf(state: FindingsState): string | null {
  if (state.status === "absent") {
    return "Findings are not served: start the dashboard with --feeds-db.";
  }
  if (state.data === null) {
    return state.status === "error"
      ? `Findings could not be read: ${state.error ?? "unknown error"}`
      : "Waiting for the hub.";
  }
  return state.data.findings.length === 0 ? "No findings recorded." : null;
}

function FindingsStreamView({ state }: FindingsStreamProps): JSX.Element {
  const rows = state.data?.findings ?? [];
  const total = state.data?.total ?? rows.length;
  const placeholder = placeholderOf(state);
  return (
    <section className="panel" aria-label="Findings stream">
      <div className="panel__head">
        <span>Findings</span>
        <span
          className="panel__count"
          title={total > rows.length ? "The newest findings; older ones stay in the log." : undefined}
        >
          {total > rows.length ? `${rows.length} of ${total}` : rows.length}
        </span>
      </div>
      <div className="panel__body" tabIndex={0}>
        {placeholder !== null ? (
          <p className="panel__placeholder">{placeholder}</p>
        ) : (
          <ul className="findings">
            {rows.map((row) => (
              <li key={row.seq} className="finding-row">
                <span className="finding-row__meta">
                  <span className="finding-row__time">{timeOf(row)}</span>
                  {row.actor === "" ? (
                    <span className="finding-row__author">—</span>
                  ) : (
                    <Ident className="finding-row__author" value={row.actor} />
                  )}
                  {row.project !== "" && <Ident className="finding-row__task" value={row.project} />}
                  {row.subkind !== "" && <span className="finding-row__axis">{row.subkind}</span>}
                  {row.claimStatus !== "" && (
                    <span className="finding-row__axis" title="Claim status">
                      {row.claimStatus}
                    </span>
                  )}
                  {row.freshness !== "" && (
                    <span
                      className={
                        row.freshness === VERIFIED_AT_SOURCE
                          ? "finding-row__axis finding-row__axis--verified"
                          : "finding-row__axis"
                      }
                      title={freshnessTitle(row.freshness)}
                    >
                      {row.freshness}
                    </span>
                  )}
                  {CLOSED_LIFECYCLES.has(row.lifecycle) && (
                    <span className="finding-row__axis finding-row__axis--closed">{row.lifecycle}</span>
                  )}
                </span>
                <span
                  className={
                    CLOSED_LIFECYCLES.has(row.lifecycle)
                      ? "finding-row__text finding-row__text--closed"
                      : "finding-row__text"
                  }
                  title={row.evidenceRef === "" ? undefined : `Evidence: ${row.evidenceRef}`}
                >
                  {row.statement}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

/** Memoised: re-renders only when its own data changes, not on the 1 s clock. */
export const FindingsStream = memo(FindingsStreamView);
