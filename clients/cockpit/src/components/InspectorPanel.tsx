// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — lazy inspector panel routing and evidence adapters

import type { JSX } from "react";
import { lazy, Suspense } from "react";

import { useCockpitI18n } from "../context/CockpitI18n";
import type { OperatorActionsState, ReceiptsState } from "../lib/auditFeeds";
import type { AttentionItem } from "../lib/attention";
import type { TimeWindow } from "../lib/brush";
import type { BranchConflictView, ClaimView } from "../lib/claims";
import type { CommunicationFilter } from "../lib/communicationFilters";
import { eventCoverageOf, type EventCoverage } from "../lib/eventCoverage";
import type { FederationState } from "../lib/federation";
import type { LogQuery } from "../lib/logQuery";
import type { MetricsState } from "../lib/metrics";
import { fleetSelectionOf } from "../lib/selection";
import type { SessionsState } from "../lib/sessions";
import type {
  CockpitSelection,
  FleetSelection,
  FleetView,
  IncidentStep,
  InspectorTab,
  ReplayState,
} from "../lib/workspace";
import type { CockpitEvent } from "../types";
import { AttentionQueue } from "./AttentionQueue";
import type { CausalityPrefill } from "./CausalityView";
import { DataCoverage } from "./DataCoverage";
import { SignalLog } from "./SignalLog";

const AuditView = lazy(async () => ({ default: (await import("./AuditView")).AuditView }));
const CausalityView = lazy(async () => ({ default: (await import("./CausalityView")).CausalityView }));
const FleetViews = lazy(async () => ({ default: (await import("./FleetViews")).FleetViews }));
const IncidentWorkspace = lazy(async () => ({ default: (await import("./IncidentWorkspace")).IncidentWorkspace }));
const MetricsPanel = lazy(async () => ({ default: (await import("./MetricsPanel")).MetricsPanel }));
const TopologyView = lazy(async () => ({ default: (await import("./TopologyView")).TopologyView }));

export interface InspectorPanelProps {
  readonly tab: InspectorTab;
  readonly fleetView: FleetView;
  readonly onFleetViewChange: (view: FleetView) => void;
  readonly fleetSelection: FleetSelection | null;
  readonly onFleetSelectionChange: (selection: FleetSelection | null) => void;
  readonly communicationFilter?: CommunicationFilter;
  readonly onCommunicationFilterChange?: ((filter: CommunicationFilter) => void) | undefined;
  readonly selection?: CockpitSelection | null;
  readonly onSelectionChange?: ((selection: CockpitSelection | null) => void) | undefined;
  readonly attention?: readonly AttentionItem[];
  readonly onInspectAgent?: ((identity: string) => void) | undefined;
  readonly onInspectTask?: ((taskId: string) => void) | undefined;
  readonly events: readonly CockpitEvent[];
  readonly window?: TimeWindow | null;
  readonly onClearWindow?: (() => void) | undefined;
  readonly provenance?: "hub" | "derived";
  readonly coverage?: EventCoverage;
  readonly query?: LogQuery;
  readonly onQueryChange?: ((query: LogQuery) => void) | undefined;
  readonly claims?: readonly ClaimView[];
  readonly conflicts?: readonly BranchConflictView[];
  readonly liveAgentCount?: number;
  readonly agents?: readonly string[];
  readonly canMessagePeer?: boolean;
  readonly onMessagePeer?: ((identity: string) => void) | undefined;
  readonly connected?: boolean;
  readonly federation?: FederationState | undefined;
  readonly metrics?: MetricsState | undefined;
  readonly sessions?: SessionsState | undefined;
  readonly receipts?: ReceiptsState | undefined;
  readonly operatorActions?: OperatorActionsState | undefined;
  readonly onOpenEvent?: ((seq: number) => void) | undefined;
  readonly incidentStep?: IncidentStep;
  readonly onIncidentStepChange?: ((step: IncidentStep) => void) | undefined;
  readonly incidentStorageKey?: string;
  readonly replay?: ReplayState;
  readonly hubVersion?: string;
  readonly configEpoch?: string;
  readonly onOpenIncidentEvidence?: ((selection: CockpitSelection) => void) | undefined;
  readonly onTabChange: (tab: InspectorTab) => void;
  readonly onSelectTask: (taskId: string) => void;
  readonly prefill: CausalityPrefill | null;
}

/** Render exactly one inspector panel while preserving deferred chunk boundaries. */
export function InspectorPanel(props: InspectorPanelProps): JSX.Element {
  const { t } = useCockpitI18n();
  const {
    tab, events, provenance = "derived", coverage, attention = [], connected = false,
    onInspectAgent, onInspectTask, onFleetSelectionChange, window = null, onClearWindow,
    onSelectTask, selection = null, onSelectionChange, query, onQueryChange,
    claims = [], agents = [], canMessagePeer = false, onMessagePeer, communicationFilter,
    onCommunicationFilterChange, onOpenEvent, fleetView, onFleetViewChange, fleetSelection,
    metrics, sessions, conflicts = [], liveAgentCount = 0, federation, receipts,
    operatorActions, incidentStorageKey = "synapse-cockpit-incident-v1:unavailable",
    incidentStep = "scope", onIncidentStepChange, replay = { mode: "live" },
    hubVersion = "", configEpoch = "", onOpenIncidentEvidence, onTabChange, prefill,
  } = props;
  const shownCoverage = coverage ?? eventCoverageOf(events, provenance);
  return (
    <>
      {(tab === "log" || tab === "fleet") && <DataCoverage coverage={shownCoverage} />}
      <div className="inspector__body" id="inspector-panel" role="tabpanel" aria-labelledby={`inspector-tab-${tab}`}>
        <Suspense fallback={<div className="panel__empty" role="status">{t("tabs.loadingPanel")}</div>}>
          {tab === "attention" ? (
            <AttentionQueue items={attention} connected={connected} onInspectAgent={onInspectAgent}
              onInspectTask={onInspectTask} onInspectRoute={(source, target) => onFleetSelectionChange({ kind: "route", source, target })} />
          ) : tab === "log" ? (
            <SignalLog events={events} window={window} onClearWindow={onClearWindow} onSelectTask={onSelectTask}
              selection={selection} onSelectEvent={(seq) => onSelectionChange?.({ kind: "event", seq })}
              provenance={provenance} {...(query !== undefined ? { query } : {})} onQueryChange={onQueryChange} />
          ) : tab === "fleet" ? (
            <FleetViews events={events} claims={claims} agents={agents} window={window} connected={connected}
              canMessage={canMessagePeer} onMessagePeer={onMessagePeer}
              {...(communicationFilter !== undefined ? { filter: communicationFilter } : {})}
              onFilterChange={onCommunicationFilterChange} onOpenEvent={onOpenEvent} view={fleetView}
              onViewChange={onFleetViewChange} selection={selection ?? fleetSelection}
              onSelectionChange={(next) => {
                if (onSelectionChange !== undefined) onSelectionChange(next);
                else onFleetSelectionChange(fleetSelectionOf(next));
              }} />
          ) : tab === "metrics" ? (
            <MetricsPanel state={metrics ?? { data: null, status: "connecting", fetchedAt: null, error: null }} sessions={sessions} />
          ) : tab === "topology" ? (
            <TopologyView claims={claims} conflicts={conflicts} liveAgentCount={liveAgentCount} connected={connected}
              {...(federation !== undefined ? { federation } : {})} />
          ) : tab === "audit" ? (
            <AuditView receipts={receipts ?? { data: null, status: "connecting", fetchedAt: null, error: null }}
              operatorActions={operatorActions ?? { data: null, status: "connecting", fetchedAt: null, error: null }}
              selection={selection} onSelectEvent={(seq) => onSelectionChange?.(seq === null ? null : { kind: "event", seq })}
              onOpenEvent={onOpenEvent ?? ((seq) => onSelectionChange?.({ kind: "event", seq }))} />
          ) : tab === "incident" ? (
            <IncidentWorkspace key={incidentStorageKey} step={incidentStep} onStepChange={(next) => onIncidentStepChange?.(next)}
              selection={selection} replay={replay} storageKey={incidentStorageKey} hubVersion={hubVersion}
              configEpoch={configEpoch} onOpenEvidence={(next) => {
                if (onOpenIncidentEvidence !== undefined) onOpenIncidentEvidence(next);
                else {
                  onSelectionChange?.(next);
                  onTabChange(next.kind === "task" ? "causality" : next.kind === "event" ? "log" : "fleet");
                }
              }} />
          ) : <CausalityView prefill={prefill} />}
        </Suspense>
      </div>
    </>
  );
}
