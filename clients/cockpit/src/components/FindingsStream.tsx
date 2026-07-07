// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the findings stream: recorded discoveries, newest first

import { memo } from "react";

import type { FindingNote } from "../lib/board";

/** Wall-clock HH:MM:SS for a finding's posted time, or a dash without one. */
function timeOf(note: FindingNote): string {
  if (note.postedAt === null) return "—";
  return new Date(note.postedAt * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

interface FindingsStreamProps {
  /** Finding notes, newest first, as the board lib derived them. */
  readonly findings: readonly FindingNote[];
  /** Whether a snapshot has arrived at all (drives the honest empty state). */
  readonly connected: boolean;
}

function FindingsStreamView({ findings, connected }: FindingsStreamProps): JSX.Element {
  return (
    <section className="panel" aria-label="Findings stream">
      <div className="panel__head">
        <span>Findings</span>
        <span className="panel__count">{findings.length}</span>
      </div>
      <div className="panel__body" tabIndex={0}>
        {!connected ? (
          <p className="panel__placeholder">Waiting for the hub.</p>
        ) : findings.length === 0 ? (
          <p className="panel__placeholder">No findings recorded.</p>
        ) : (
          <ul className="findings">
            {findings.map((note, index) => (
              <li key={`${note.taskId}:${note.postedAt ?? index}`} className="finding-row">
                <span className="finding-row__meta">
                  <span className="finding-row__time">{timeOf(note)}</span>
                  <span className="finding-row__author" title={note.author}>
                    {note.author === "" ? "—" : note.author}
                  </span>
                  {note.taskId !== "" && (
                    <span className="finding-row__task" title={note.taskId}>
                      {note.taskId}
                    </span>
                  )}
                </span>
                <span className="finding-row__text">{note.text}</span>
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
