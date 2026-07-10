// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the time-travel bar: scrub the whole fleet, not just the log

import type { JSX } from "react";
import type { FleetStateAt } from "../lib/stateAt";

function stampOf(ts: number): string {
  if (ts === 0) return "—";
  return new Date(ts * 1000).toLocaleString([], {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

interface TimeTravelBarProps {
  /** Whether the mode is armed. */
  readonly on: boolean;
  /** The scrub position (sequence). */
  readonly seq: number;
  /** The reconstructed moment, or null while fetching. */
  readonly state: FleetStateAt | null;
  /** A fetch problem, stated instead of the moment. */
  readonly note: string | null;
  readonly onToggle: () => void;
  readonly onScrub: (seq: number) => void;
}

/**
 * The loudest banner in the cockpit when armed: the claims board, task
 * board, and topology below it show a RECONSTRUCTED moment, not now. The
 * bar states the moment (seq + its own timestamp) and the server's honest
 * scope (presence is not journalled) verbatim. The spine, the log, and the
 * roster stay live — the two truths never blend silently.
 */
export function TimeTravelBar({ on, seq, state, note, onToggle, onScrub }: TimeTravelBarProps): JSX.Element {
  return (
    <div className={`timetravel${on ? " timetravel--armed" : ""}`}>
      <button
        type="button"
        className="log-controls__toggle"
        onClick={onToggle}
        aria-pressed={on}
        title="Reconstruct the claims and board as of any point in the durable log"
      >
        {on ? "back to now" : "time travel"}
      </button>
      {on && (
        <>
          <input
            type="range"
            className="log-scrub__slider"
            min={1}
            max={Math.max(1, state?.logEndSeq ?? seq)}
            value={seq}
            onChange={(change) => onScrub(Number(change.target.value))}
            aria-label="Reconstruction position in the durable log, by sequence"
          />
          <span className="timetravel__label">
            {note !== null
              ? note
              : state === null
                ? "reconstructing…"
                : `claims + board as of seq ${state.asOfSeq} · ${stampOf(state.asOfTs)} · roster stays live (presence not journalled)`}
          </span>
        </>
      )}
    </div>
  );
}
