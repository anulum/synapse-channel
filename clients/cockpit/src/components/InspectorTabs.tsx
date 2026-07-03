// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — tab switch between the signal log and the causality inspector

import { useCallback, useState } from "react";
import { windowEdgeLabel, type TimeWindow } from "../lib/brush";
import type { BranchConflictView, ClaimView } from "../lib/claims";
import type { FederationState } from "../lib/federation";
import type { MetricsState } from "../lib/metrics";
import type { LogQuery } from "../lib/logQuery";
import type { CockpitEvent } from "../types";
import { CausalityView, type CausalityPrefill } from "./CausalityView";
import { SignalLog } from "./SignalLog";
import { MetricsPanel } from "./MetricsPanel";
import { TopologyView } from "./TopologyView";

type InspectorTab = "log" | "topology" | "metrics" | "causality";

interface InspectorTabsProps {
  /** Events for the signal-log tab, newest first. */
  readonly events: readonly CockpitEvent[];
  /** The brushed spine window filtering the log, or null. */
  readonly window?: TimeWindow | null;
  /** Clears the brushed window. */
  readonly onClearWindow?: (() => void) | undefined;
  /** Where the events come from: the hub's log or the snapshot derivation. */
  readonly provenance?: "hub" | "derived";
  /** The operator's log query, owned by the caller (URL-shareable). */
  readonly query?: LogQuery;
  /** Query updates from the log's controls. */
  readonly onQueryChange?: ((query: LogQuery) => void) | undefined;
  /** Claim rows for the topology tab. */
  readonly claims?: readonly ClaimView[];
  /** Advisory branch conflicts for the topology tab. */
  readonly conflicts?: readonly BranchConflictView[];
  /** Live roster size (topology states the idle remainder). */
  readonly liveAgentCount?: number;
  /** Whether a snapshot has arrived at all. */
  readonly connected?: boolean;
  /** The federation posture feed, for the topology tab's peering band. */
  readonly federation?: FederationState | undefined;
  /** The log-pulse metrics feed, for the metrics tab. */
  readonly metrics?: MetricsState | undefined;
}

export function InspectorTabs({
  events,
  window = null,
  onClearWindow,
  provenance = "derived",
  query,
  onQueryChange,
  claims = [],
  conflicts = [],
  liveAgentCount = 0,
  connected = false,
  federation,
  metrics,
}: InspectorTabsProps): JSX.Element {
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
          aria-selected={tab === "topology"}
          className={`inspector__tab${tab === "topology" ? " inspector__tab--active" : ""}`}
          onClick={() => setTab("topology")}
        >
          topology
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "metrics"}
          className={`inspector__tab${tab === "metrics" ? " inspector__tab--active" : ""}`}
          onClick={() => setTab("metrics")}
        >
          metrics
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
            provenance={provenance}
            {...(query !== undefined ? { query } : {})}
            onQueryChange={onQueryChange}
          />
        ) : tab === "metrics" ? (
          <MetricsPanel
            state={metrics ?? { data: null, status: "connecting", fetchedAt: null, error: null }}
          />
        ) : tab === "topology" ? (
          <TopologyView
            claims={claims}
            conflicts={conflicts}
            liveAgentCount={liveAgentCount}
            connected={connected}
            {...(federation !== undefined ? { federation } : {})}
          />
        ) : (
          <CausalityView prefill={prefill} />
        )}
      </div>
    </div>
  );
}
