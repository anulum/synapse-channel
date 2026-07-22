// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — multi-view fleet communication instrument

import type { JSX, KeyboardEvent } from "react";
import { memo, useCallback, useMemo, useRef } from "react";

import { useCockpitI18n } from "../context/CockpitI18n";
import type { TimeWindow } from "../lib/brush";
import type { ClaimView } from "../lib/claims";
import {
  COMMUNICATION_HEALTH_FILTERS,
  COMMUNICATION_QUERY_LIMIT,
  DEFAULT_COMMUNICATION_FILTER,
  communicationFilterIsActive,
  communicationHealthFilter,
  filterCommunicationModel,
  type CommunicationFilter,
} from "../lib/communicationFilters";
import {
  deriveCommunicationModel,
  deriveConversationDetail,
  type CommunicationEdge,
} from "../lib/communications";
import { deriveFleetTimeline, deriveProjectFlow } from "../lib/fleetVisuals";
import type { MessageKey } from "../lib/i18n";
import {
  sendOperatorResponse,
  type MessageResponseInput,
  type OperatorActionResult,
} from "../lib/operatorActions";
import {
  FLEET_VIEWS,
  type CockpitSelection,
  type FleetView,
} from "../lib/workspace";
import type { CockpitEvent } from "../types";
import {
  FleetMatrixView,
  FleetProjectsView,
  FleetWebView,
} from "./FleetCommunicationViews";
import {
  FleetEdgeDetail,
  FleetNodeDetail,
  FleetProjectDetail,
} from "./FleetConversationDetail";
import { ProjectFlowView, TimelineView } from "./FleetVisualModes";

interface FleetViewsProps {
  readonly view: FleetView;
  readonly onViewChange: (view: FleetView) => void;
  readonly selection: CockpitSelection | null;
  readonly onSelectionChange: (selection: CockpitSelection | null) => void;
  readonly events: readonly CockpitEvent[];
  readonly claims: readonly ClaimView[];
  readonly agents: readonly string[];
  readonly window: TimeWindow | null;
  readonly connected: boolean;
  readonly canMessage: boolean;
  readonly onMessagePeer?: ((identity: string) => void) | undefined;
  readonly respondToMessage?: ((input: MessageResponseInput) => Promise<OperatorActionResult>) | undefined;
  readonly filter?: CommunicationFilter;
  readonly onFilterChange?: ((filter: CommunicationFilter) => void) | undefined;
  readonly onOpenEvent?: ((seq: number) => void) | undefined;
}

const VIEW_KEYS: Readonly<Record<FleetView, MessageKey>> = {
  web: "fleet.view.web",
  matrix: "fleet.view.matrix",
  projects: "fleet.view.projects",
  timeline: "fleet.view.timeline",
  flow: "fleet.view.flow",
};

function FleetViewsComponent({
  view,
  onViewChange,
  selection,
  onSelectionChange,
  events,
  claims,
  agents,
  window,
  connected,
  canMessage,
  onMessagePeer,
  respondToMessage = sendOperatorResponse,
  filter = DEFAULT_COMMUNICATION_FILTER,
  onFilterChange,
  onOpenEvent,
}: FleetViewsProps): JSX.Element {
  const { t } = useCockpitI18n();
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const model = useMemo(
    () => deriveCommunicationModel(events, claims, agents, window),
    [events, claims, agents, window],
  );
  const filteredModel = useMemo(
    () => filterCommunicationModel(model, filter),
    [filter, model],
  );
  const filterActive = communicationFilterIsActive(filter);
  const filtersMutable = onFilterChange !== undefined;
  const communicationView = view === "web" || view === "matrix" || view === "projects";
  const visualModel = communicationView ? filteredModel : model;
  const timeline = useMemo(() => deriveFleetTimeline(events, window), [events, window]);
  const flow = useMemo(() => deriveProjectFlow(events, claims, window), [events, claims, window]);
  const selectedNode = selection?.kind === "agent" ? model.nodes.find((node) => node.id === selection.id) : undefined;
  const selectedProject =
    selection?.kind === "project" ? model.projects.find((project) => project.id === selection.id) : undefined;
  const selectedEdge =
    selection?.kind === "route"
      ? model.edges.find((edge) => edge.source === selection.source && edge.target === selection.target)
      : undefined;
  const selectedEdgeVisible =
    selectedEdge !== undefined && filteredModel.edges.some((edge) => edge.id === selectedEdge.id);
  const selectedConversation = useMemo(
    () =>
      selection?.kind === "route" ? deriveConversationDetail(events, selection.source, selection.target, window) : [],
    [events, selection, window],
  );
  const failed = visualModel.edges.filter((edge: CommunicationEdge) => edge.health === "failed").length;
  const updateFilter = (change: Partial<CommunicationFilter>): void => {
    onFilterChange?.({ ...filter, ...change });
  };
  const clearFilter = (): void => onFilterChange?.(DEFAULT_COMMUNICATION_FILTER);

  const onViewKeyDown = useCallback(
    (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
      let nextIndex: number | null = null;
      if (event.key === "ArrowRight") nextIndex = (index + 1) % FLEET_VIEWS.length;
      else if (event.key === "ArrowLeft") nextIndex = (index - 1 + FLEET_VIEWS.length) % FLEET_VIEWS.length;
      else if (event.key === "Home") nextIndex = 0;
      else if (event.key === "End") nextIndex = FLEET_VIEWS.length - 1;
      if (nextIndex === null) return;
      event.preventDefault();
      const nextView = FLEET_VIEWS[nextIndex];
      if (nextView === undefined) return;
      onViewChange(nextView);
      tabRefs.current[nextIndex]?.focus();
    },
    [onViewChange],
  );

  return (
    <section className="panel fleet-views" aria-label={t("fleet.views.aria")}>
      <div className="fleet-views__toolbar">
        <div className="fleet-views__switch" role="tablist" aria-label={t("fleet.viewSwitch.aria")}>
          {FLEET_VIEWS.map((candidate, index) => (
            <button
              key={candidate}
              ref={(element) => {
                tabRefs.current[index] = element;
              }}
              id={`fleet-view-tab-${candidate}`}
              type="button"
              role="tab"
              tabIndex={view === candidate ? 0 : -1}
              aria-selected={view === candidate}
              aria-controls="fleet-view-panel"
              className={view === candidate ? "fleet-views__view fleet-views__view--active" : "fleet-views__view"}
              onClick={() => onViewChange(candidate)}
              onKeyDown={(event) => onViewKeyDown(event, index)}
            >
              {t(VIEW_KEYS[candidate])}
            </button>
          ))}
        </div>
        <div className="fleet-views__summary">
          <span>{t("fleet.summary.identities", { count: visualModel.nodes.length })}</span>
          <span>{t("fleet.summary.messages", { count: visualModel.messages })}</span>
          <span className={failed > 0 ? "fleet-views__alert" : ""}>
            {t("fleet.summary.troubledLinks", { count: failed })}
          </span>
        </div>
      </div>
      <div className="fleet-filters" aria-label={t("fleet.filters.aria")}>
        <label>
          <span>{t("fleet.filters.query")}</span>
          <input
            type="search"
            value={filter.query}
            maxLength={COMMUNICATION_QUERY_LIMIT}
            placeholder={t("fleet.filters.placeholder")}
            disabled={!filtersMutable}
            onChange={(event) => updateFilter({ query: event.target.value })}
          />
        </label>
        <label>
          <span>{t("fleet.filters.health")}</span>
          <select
            value={filter.health}
            disabled={!filtersMutable}
            onChange={(event) => updateFilter({
              health: communicationHealthFilter(event.target.value),
            })}
          >
            {COMMUNICATION_HEALTH_FILTERS.map((health) => (
              <option key={health} value={health}>
                {health === "all" ? t("fleet.filters.all") : health === "healthy" ? "delivered" : health}
              </option>
            ))}
          </select>
        </label>
        <button type="button" onClick={clearFilter} disabled={!filtersMutable || !filterActive}>
          {t("fleet.filters.clear")}
        </button>
        <output aria-live="polite">
          {communicationView
            ? t("fleet.filters.results", {
                shown: filteredModel.edges.length,
                total: model.edges.length,
                messages: filteredModel.messages,
              })
            : t("fleet.filters.paused")}
        </output>
      </div>
      {!connected ? (
        <p className="panel__placeholder panel__placeholder--padded">{t("fleet.waiting")}</p>
      ) : (
        <div className="fleet-views__stage">
          <div
            className="fleet-views__visual"
            id="fleet-view-panel"
            role="tabpanel"
            aria-labelledby={`fleet-view-tab-${view}`}
          >
            {view === "web" && visualModel.messages > 0 ? (
              <FleetWebView
                model={visualModel}
                selection={selection}
                onSelectNode={(id) => onSelectionChange({ kind: "agent", id })}
                onSelectEdge={(source, target) => onSelectionChange({ kind: "route", source, target })}
              />
            ) : view === "matrix" && visualModel.messages > 0 ? (
              <FleetMatrixView
                model={visualModel}
                selection={selection}
                onSelect={(source, target) => onSelectionChange({ kind: "route", source, target })}
              />
            ) : view === "projects" && visualModel.projects.length > 0 ? (
              <FleetProjectsView
                projects={visualModel.projects}
                selection={selection}
                onSelect={(id) => onSelectionChange({ kind: "project", id })}
              />
            ) : view === "timeline" && timeline.points.length > 0 ? (
              <TimelineView
                timeline={timeline}
                events={events}
                selection={selection}
                onSelect={(seq) => onSelectionChange({ kind: "event", seq })}
              />
            ) : view === "flow" && flow.links.length > 0 ? (
              <ProjectFlowView
                model={flow}
                selection={selection}
                onSelectProject={(id) => onSelectionChange({ kind: "project", id })}
                onSelectEvent={(seq) => onSelectionChange({ kind: "event", seq })}
              />
            ) : (
              <p className="panel__placeholder panel__placeholder--padded">{t("fleet.empty")}</p>
            )}
          </div>
          {selectedEdge !== undefined ? (
            <FleetEdgeDetail
              key={`${selectedEdge.source}\u0000${selectedEdge.target}`}
              edge={selectedEdge}
              messages={selectedConversation}
              canRespond={canMessage}
              respond={respondToMessage}
              outsideFilter={communicationView && filterActive && !selectedEdgeVisible}
              onClearFilter={clearFilter}
              onOpenEvent={onOpenEvent}
            />
          ) : selectedNode !== undefined ? (
            <FleetNodeDetail node={selectedNode} canMessage={canMessage} onMessagePeer={onMessagePeer} />
          ) : selectedProject !== undefined ? (
            <FleetProjectDetail project={selectedProject} />
          ) : null}
        </div>
      )}
    </section>
  );
}

export const FleetViews = memo(FleetViewsComponent);
