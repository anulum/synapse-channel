// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — accessible activity-spine composition

import type { JSX } from "react";

import { useActivitySpine } from "../hooks/useActivitySpine";
import { LANES } from "../lib/events";
import type { TimeWindow } from "../lib/brush";
import type { CockpitSelection } from "../lib/workspace";
import type { CockpitEvent, EventSource } from "../types";

interface SpineProps {
  /** Real events to plot; the caller owns the source lifecycle. */
  readonly source?: EventSource | undefined;
  /** Reports the impulse nearest the pointer, or null when inspection ends. */
  readonly onInspect?: ((event: CockpitEvent | null) => void) | undefined;
  /** Reports an absolute-time brush selected by pointer or keyboard. */
  readonly onBrush?: ((window: TimeWindow | null) => void) | undefined;
  /** Caller-owned brushed window. */
  readonly brush?: TimeWindow | null | undefined;
  /** Shared workspace selection to ring where the spine has direct evidence. */
  readonly workspaceSelection?: CockpitSelection | null | undefined;
}

function timeOf(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

/** The DOM peer for the canvas instrument; rendering and input live elsewhere. */
export function ActivitySpine(props: SpineProps): JSX.Element {
  const { canvasRef, canvas, hover } = useActivitySpine(props);
  return (
    <section className="spine" aria-label="Fleet activity spine">
      <canvas
        ref={canvasRef}
        className="spine__canvas"
        tabIndex={0}
        aria-label="Activity spine. Drag or use the arrow keys to brush a time window that filters the signal log; brackets resize it, Escape clears it. The signal log table is the accessible reading of the same events."
      />
      <div className="spine__lanes" aria-hidden="true">
        {LANES.map((lane) => (
          <div key={lane} className="spine__lane-label">{lane}</div>
        ))}
      </div>
      <div className="spine__legend" aria-hidden="true">
        <span className="spine-key spine-key--info">claim · finding</span>
        <span className="spine-key spine-key--healthy">release · done</span>
        <span className="spine-key spine-key--warn">lease expiry</span>
        <span className="spine-key spine-key--dim">chatter</span>
        <span className="spine-key spine-key--critical">conflict</span>
      </div>
      {hover !== null && (
        <div
          className="spine__tooltip"
          style={{
            left: Math.min(hover.x + 12, Math.max(0, (canvas?.clientWidth ?? 0) - 260)),
            top: Math.min(hover.y + 10, 96),
          }}
          role="status"
        >
          <span className="spine__tooltip-meta">
            {timeOf(hover.event.ts)} · {hover.event.kind}
            {hover.event.actor !== "" && ` · ${hover.event.actor}`}
          </span>
          <span className="spine__tooltip-label">{hover.event.label}</span>
        </div>
      )}
    </section>
  );
}
