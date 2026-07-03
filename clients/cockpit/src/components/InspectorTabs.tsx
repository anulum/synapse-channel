// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — tab switch between the signal log and the causality inspector

import { useState } from "react";
import type { CockpitEvent } from "../types";
import { CausalityView } from "./CausalityView";
import { SignalLog } from "./SignalLog";

type InspectorTab = "log" | "causality";

interface InspectorTabsProps {
  /** Derived transition events for the signal-log tab, newest first. */
  readonly events: readonly CockpitEvent[];
}

export function InspectorTabs({ events }: InspectorTabsProps): JSX.Element {
  const [tab, setTab] = useState<InspectorTab>("log");

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
      </div>
      <div className="inspector__body">
        {tab === "log" ? <SignalLog events={events} /> : <CausalityView />}
      </div>
    </div>
  );
}
