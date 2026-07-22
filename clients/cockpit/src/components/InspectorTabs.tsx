// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — tab switch between the signal log and the causality inspector

import type { JSX, KeyboardEvent } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { OperatorActionsState, ReceiptsState } from "../lib/auditFeeds";
import { windowEdgeLabel, type TimeWindow } from "../lib/brush";
import type { BranchConflictView, ClaimView } from "../lib/claims";
import type { FederationState } from "../lib/federation";
import type { MetricsState } from "../lib/metrics";
import type { SessionsState } from "../lib/sessions";
import type { LogQuery } from "../lib/logQuery";
import type { CockpitEvent } from "../types";
import {
  INSPECTOR_TABS,
  type FleetSelection,
  type FleetView,
  type InspectorTab,
} from "../lib/workspace";
import { AuditView } from "./AuditView";
import { CausalityView, type CausalityPrefill } from "./CausalityView";
import { FleetViews } from "./FleetViews";
import { SignalLog } from "./SignalLog";
import { MetricsPanel } from "./MetricsPanel";
import { TopologyView } from "./TopologyView";

interface InspectorTabsProps {
  readonly tab: InspectorTab;
  readonly onTabChange: (tab: InspectorTab) => void;
  readonly fleetView: FleetView;
  readonly onFleetViewChange: (view: FleetView) => void;
  readonly fleetSelection: FleetSelection | null;
  readonly onFleetSelectionChange: (selection: FleetSelection | null) => void;
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
  /** Exact live/claim identities included even when quiet in the event window. */
  readonly agents?: readonly string[];
  /** Whether the current principal may open the governed message composer. */
  readonly canMessagePeer?: boolean;
  /** Opens the governed message composer for an exact peer. */
  readonly onMessagePeer?: ((identity: string) => void) | undefined;
  /** Whether a snapshot has arrived at all. */
  readonly connected?: boolean;
  /** The federation posture feed, for the topology tab's peering band. */
  readonly federation?: FederationState | undefined;
  /** The log-pulse metrics feed, for the metrics tab. */
  readonly metrics?: MetricsState | undefined;
  /** The sessions/cost feed, rendered inside the metrics tab. */
  readonly sessions?: SessionsState | undefined;
  /** Universal receipt history for the audit tab. */
  readonly receipts?: ReceiptsState | undefined;
  /** Governed operator-relay history for the audit tab. */
  readonly operatorActions?: OperatorActionsState | undefined;
  /** External trace request (e.g. a drawer's hop); nonce forces re-fire. */
  readonly traceRequest?: { readonly subject: string; readonly nonce: number } | undefined;
}

export function InspectorTabs({
  tab,
  onTabChange,
  fleetView,
  onFleetViewChange,
  fleetSelection,
  onFleetSelectionChange,
  events,
  window = null,
  onClearWindow,
  provenance = "derived",
  query,
  onQueryChange,
  claims = [],
  conflicts = [],
  liveAgentCount = 0,
  agents = [],
  canMessagePeer = false,
  onMessagePeer,
  connected = false,
  federation,
  metrics,
  sessions,
  receipts,
  operatorActions,
  traceRequest,
}: InspectorTabsProps): JSX.Element {
  const [prefill, setPrefill] = useState<CausalityPrefill | null>(null);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  // Master-detail hop: a task named by a log row jumps straight into the
  // causality inspector, subject adopted and traced.
  const onSelectTask = useCallback((taskId: string) => {
    setPrefill((current) => ({ subject: taskId, nonce: (current?.nonce ?? 0) + 1 }));
    onTabChange("causality");
  }, [onTabChange]);

  const onTabKeyDown = useCallback(
    (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
      let nextIndex: number | null = null;
      if (event.key === "ArrowRight") nextIndex = (index + 1) % INSPECTOR_TABS.length;
      else if (event.key === "ArrowLeft") nextIndex = (index - 1 + INSPECTOR_TABS.length) % INSPECTOR_TABS.length;
      else if (event.key === "Home") nextIndex = 0;
      else if (event.key === "End") nextIndex = INSPECTOR_TABS.length - 1;
      if (nextIndex === null) return;
      event.preventDefault();
      const nextTab = INSPECTOR_TABS[nextIndex];
      if (nextTab === undefined) return;
      onTabChange(nextTab);
      tabRefs.current[nextIndex]?.focus();
    },
    [onTabChange],
  );

  // A drawer (or any outside caller) can steer the inspector the same way a
  // log row does; the nonce lets the same subject fire twice.
  useEffect(() => {
    if (traceRequest === undefined) return;
    onSelectTask(traceRequest.subject);
  }, [traceRequest, onSelectTask]);

  return (
    <div className="inspector" role="region" aria-label="Inspector">
      <div className="inspector__tabs" role="tablist" aria-label="Inspector">
        {INSPECTOR_TABS.map((candidate, index) => (
          <button
            key={candidate}
            ref={(element) => {
              tabRefs.current[index] = element;
            }}
            id={`inspector-tab-${candidate}`}
            type="button"
            role="tab"
            tabIndex={tab === candidate ? 0 : -1}
            aria-selected={tab === candidate}
            aria-controls="inspector-panel"
            className={`inspector__tab${tab === candidate ? " inspector__tab--active" : ""}`}
            onClick={() => onTabChange(candidate)}
            onKeyDown={(event) => onTabKeyDown(event, index)}
          >
            {candidate === "log" ? "signal log" : candidate}
            {candidate === "log" && <span className="inspector__tab-count">{events.length}</span>}
          </button>
        ))}
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
      <div
        className="inspector__body"
        id="inspector-panel"
        role="tabpanel"
        aria-labelledby={`inspector-tab-${tab}`}
      >
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
        ) : tab === "fleet" ? (
          <FleetViews
            events={events}
            claims={claims}
            agents={agents}
            window={window}
            connected={connected}
            canMessage={canMessagePeer}
            onMessagePeer={onMessagePeer}
            view={fleetView}
            onViewChange={onFleetViewChange}
            selection={fleetSelection}
            onSelectionChange={onFleetSelectionChange}
          />
        ) : tab === "metrics" ? (
          <MetricsPanel
            state={metrics ?? { data: null, status: "connecting", fetchedAt: null, error: null }}
            sessions={sessions}
          />
        ) : tab === "topology" ? (
          <TopologyView
            claims={claims}
            conflicts={conflicts}
            liveAgentCount={liveAgentCount}
            connected={connected}
            {...(federation !== undefined ? { federation } : {})}
          />
        ) : tab === "audit" ? (
          <AuditView
            receipts={receipts ?? { data: null, status: "connecting", fetchedAt: null, error: null }}
            operatorActions={
              operatorActions ?? { data: null, status: "connecting", fetchedAt: null, error: null }
            }
          />
        ) : (
          <CausalityView prefill={prefill} />
        )}
      </div>
    </div>
  );
}
