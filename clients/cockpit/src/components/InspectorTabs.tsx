// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — tab switch between the signal log and the causality inspector

import { useCallback, useState } from "react";
import { windowEdgeLabel, type TimeWindow } from "../lib/brush";
import type { CockpitEvent } from "../types";
import { CausalityView, type CausalityPrefill } from "./CausalityView";
import { SignalLog } from "./SignalLog";

type InspectorTab = "log" | "causality";

interface InspectorTabsProps {
  /** Derived transition events for the signal-log tab, newest first. */
  readonly events: readonly CockpitEvent[];
  /** The brushed spine window filtering the log, or null. */
  readonly window?: TimeWindow | null;
  /** Clears the brushed window. */
  readonly onClearWindow?: (() => void) | undefined;
}

export function InspectorTabs({ events, window = null, onClearWindow }: InspectorTabsProps): JSX.Element {
  const [tab, setTab] = useState<InspectorTab>("log");
  const [prefill, setPrefill] = useState<CausalityPrefill | null>(null);

  // Master-detail hop: a task named by a log row jumps straight into the
  // causality inspector, subject adopted and traced.
  const onSelectTask = useCallback((taskId: string) => {
    setPrefill((current) => ({ subject: taskId, nonce: (current?.nonce ?? 0) + 1 }));
    setTab("causality");
  }, []);

  return (
    <div className="inspector">
      <div className="inspector__tabs" role="tablist" aria-label="Inspector">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "log"}
          className={`inspector__tab${tab === "log" ? " inspector__tab--active" : ""}`}
          onClick={() => setTab("log")}
        >
          signal log <span className="inspector__tab-count">{events.length}</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "causality"}
          className={`inspector__tab${tab === "causality" ? " inspector__tab--active" : ""}`}
          onClick={() => setTab("causality")}
        >
          causality
        </button>
        {window !== null && (
          <span className="inspector__brush">
            {`${windowEdgeLabel(window.fromTs)}–${windowEdgeLabel(window.toTs)}`}
            <button
              type="button"
              className="panel__clear"
              onClick={() => onClearWindow?.()}
              aria-label="Clear the brushed window"
            >
              clear
            </button>
          </span>
        )}
      </div>
      <div className="inspector__body">
        {tab === "log" ? (
          <SignalLog
            events={events}
            window={window}
            onClearWindow={onClearWindow}
            onSelectTask={onSelectTask}
          />
        ) : (
          <CausalityView prefill={prefill} />
        )}
      </div>
    </div>
  );
}
