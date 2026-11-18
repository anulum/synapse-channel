// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — cockpit app shell

import type { JSX } from "react";
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import { ActivitySpine } from "./components/ActivitySpine";
import { AuthVeil } from "./components/AuthVeil";
import { ClaimsBoard } from "./components/ClaimsBoard";
import { DetailDrawer } from "./components/DetailDrawer";
import { FederationRow } from "./components/FederationRow";
import { FindingsStream } from "./components/FindingsStream";
import { FleetBand, FleetBoardPanel, FleetClaimsPanel, FleetFeedPanel } from "./components/FleetMirrorScope";
import { FleetRoster } from "./components/FleetRoster";
import { GuideDrawer } from "./components/GuideDrawer";
import { Hud } from "./components/Hud";
import { HostSessions } from "./components/HostSessions";
import { InspectorTabs } from "./components/InspectorTabs";
import { InstallChip } from "./components/InstallChip";
import { MobileNav, type MobileSegment } from "./components/MobileNav";
import { Palette } from "./components/Palette";
import { PanelBoundary } from "./components/PanelBoundary";
import { ReliabilityPanel } from "./components/ReliabilityPanel";
import { ReplayWorkbench } from "./components/ReplayWorkbench";
import { RiskRail } from "./components/RiskRail";
import { RoleBadge } from "./components/RoleBadge";
import { PeerBand, PeerFeedPanel } from "./components/PeerScope";
import { ScopePicker } from "./components/ScopePicker";
import { ScopeProvider } from "./components/ScopeTag";
import { SelectionBar } from "./components/SelectionBar";
import { SetupAssistant } from "./components/SetupAssistant";
import { TaskBoard } from "./components/TaskBoard";
import { ToastStack } from "./components/ToastStack";
import { useCockpitI18n } from "./context/CockpitI18n";
import { useCockpitFeeds } from "./hooks/useCockpitFeeds";
import { useCockpitOverlays } from "./hooks/useCockpitOverlays";
import { useCockpitPreferences } from "./hooks/useCockpitPreferences";
import { useCockpitReplay } from "./hooks/useCockpitReplay";
import { useCockpitScope } from "./hooks/useCockpitScope";
import { useCockpitToasts } from "./hooks/useCockpitToasts";
import { useCockpitViewModel } from "./hooks/useCockpitViewModel";
import { useCockpitWorkspace } from "./hooks/useCockpitWorkspace";
import { useDashboardAccess } from "./hooks/useDashboardAccess";
import { useFleetMirror, useFleetMirrorAccess } from "./hooks/useFleetMirror";
import { useServedSurfaces } from "./hooks/useServedSurfaces";
import { capabilitiesOf } from "./lib/access";
import { boardTruncation } from "./lib/board";
import type { TimeWindow } from "./lib/brush";
import { agentDetail, taskDetail } from "./lib/detail";
import { WHOLE_HUB } from "./lib/scope";
import { fleetSelectionOf } from "./lib/selection";
import { isLoopbackHostname } from "./lib/setupAssistant";
import {
  cockpitAuthSnapshot,
  lockCockpit,
  subscribeCockpitAuth,
  unlockCockpit,
} from "./lib/auth";

/** Compose the authenticated cockpit from focused state owners and panel surfaces. */
export function App(): JSX.Element {
  const { t } = useCockpitI18n();
  const {
    workspace,
    setPanel,
    setFleetView,
    setSelection,
    setPanelSelection,
    setFleetSelection,
    setReplay,
    replaceReplay,
    setIncidentStep,
    setCommunicationFilter,
    setOperation,
  } = useCockpitWorkspace();
  const auth = useSyncExternalStore(
    subscribeCockpitAuth,
    cockpitAuthSnapshot,
    cockpitAuthSnapshot,
  );
  const authBlocked = auth.phase === "locked";
  const access = useDashboardAccess(authBlocked, auth.revision);
  const shellBlocked = authBlocked || access.phase === "loading";
  const { scope, setScope } = useCockpitScope();
  // A peer scope shows an observed hub, and the Fleet scope Fleet's export: both read only,
  // with no feed of this hub and no time travel.
  const peerHub = scope.kind === "peer" ? scope.hub : null;
  const fleetScope = scope.kind === "fleet";
  const observed = peerHub !== null || fleetScope;
  const fleetOffered = useFleetMirrorAccess(shellBlocked, auth.revision);
  const mirror = useFleetMirror(fleetScope && !shellBlocked, auth.revision);
  const [fleetNotice, setFleetNotice] = useState<string | null>(null);
  useEffect(() => {
    if (!fleetScope || (mirror.status !== "withdrawn" && mirror.status !== "absent")) return;
    setFleetNotice(t(mirror.status === "withdrawn" ? "scope.fleetWithdrawn" : "scope.fleetAbsent"));
    setScope(WHOLE_HUB);
  }, [fleetScope, mirror.status, setScope, t]);
  const feeds = useCockpitFeeds(shellBlocked, auth.revision, scope);
  const preferences = useCockpitPreferences();
  const [brush, setBrush] = useState<TimeWindow | null>(null);
  const [mobileSegment, setMobileSegment] = useState<MobileSegment>("signals");
  const [hostSessionsOpen, setHostSessionsOpen] = useState(false);
  const replay = useCockpitReplay({
    blocked: shellBlocked || observed,
    replay: workspace.replay,
    maximumSequence: feeds.coverage.maxSeq,
    setReplay,
  });
  const view = useCockpitViewModel({
    feeds,
    replaySlot: replay.slotB,
    travelling: replay.travelling,
    focus: preferences.focus,
    brush,
  });
  const capabilities = capabilitiesOf(access);
  const overlays = useCockpitOverlays({
    blocked: shellBlocked,
    authBlocked,
    capabilities,
    writable: !observed,
    accessChangedMessage: t("app.accessChanged"),
    agents: view.roster.map((entry) => entry.agent),
    tasks: view.liveBoard.map((task) => task.taskId),
    setSelection,
    setFocus: preferences.setFocus,
    toggleTheme: preferences.toggleTheme,
    toggleDensity: preferences.toggleDensity,
    toggleTravel: replay.toggleTravel,
  });
  const served = useServedSurfaces(overlays.setupOpen && !shellBlocked, auth.revision);
  const toastController = useCockpitToasts({
    blocked: shellBlocked,
    snapshot: feeds.snap.snapshot,
    board: view.liveBoard,
    conflicts: view.liveConflicts,
    deadLetters: view.deadLetters,
  });

  useEffect(() => {
    if (shellBlocked) setBrush(null);
  }, [shellBlocked]);

  const onSelectKpi = useCallback((label: string) => {
    const kinds =
      label === "claims held"
        ? (["claim", "lease", "release"] as const)
        : label === "risk signals"
          ? (["conflict"] as const)
          : label === "agents online"
            ? (["presence"] as const)
            : null;
    preferences.setLogQuery({
      text: "",
      kinds: kinds === null ? null : [...kinds],
      order: "newest",
      view: "flat",
    });
  }, [preferences.setLogQuery]);

  const onClearWindow = useCallback(() => setBrush(null), []);

  /** A panel of this hub's feeds in an observed scope: the peer's notice, or the Fleet mirror's. */
  const observedPanel = (label: string, title: string): JSX.Element =>
    peerHub !== null ? (
      <PeerFeedPanel label={label} title={title} hub={peerHub} />
    ) : (
      <FleetFeedPanel label={label} title={title} />
    );

  if (authBlocked) {
    return <AuthVeil reason={auth.reason} onUnlock={unlockCockpit} />;
  }
  if (access.phase === "loading") {
    return <main className="access-probe" role="status">{t("app.accessChecking")}</main>;
  }

  return (
    <ScopeProvider scope={scope} travelling={replay.travelling}>
      <div className="shell">
        <span className="visually-hidden" role="status" aria-live="polite">
          {overlays.accessNotice}
        </span>
        <Hud
          kpis={fleetScope ? [] : feeds.kpis}
          live={fleetScope ? mirror.status === "live" : feeds.snap.status === "live"}
          stamp={feeds.stamp}
          transport={observed ? "observed" : feeds.transport.status}
          onSelect={onSelectKpi}
          theme={preferences.theme}
          onToggleTheme={preferences.toggleTheme}
          focus={preferences.focus}
          onFocusChange={preferences.setFocus}
          rosterNames={view.roster.map((entry) => entry.agent)}
          density={preferences.density}
          onToggleDensity={preferences.toggleDensity}
          scopeControl={
            <ScopePicker
              scope={scope}
              counts={feeds.snap.snapshot?.scope ?? null}
              onChange={setScope}
              fleetOffered={fleetOffered}
            />
          }
          accessControl={
            <RoleBadge
              access={access}
              onChangeAccess={() =>
                lockCockpit("Paste another dashboard bearer to change access.")
              }
            />
          }
          commandTriggerRef={overlays.commandTrigger}
          onOpenPalette={overlays.openPalette}
          guideTriggerRef={overlays.guideTrigger}
          onOpenGuide={overlays.openGuide}
          setupTriggerRef={overlays.setupTrigger}
          onOpenSetup={overlays.openSetup}
        />
        <SelectionBar
          selection={workspace.selection}
          focus={preferences.focus}
          window={brush}
          onClearSelection={() => setSelection(null)}
          onClearFocus={() => preferences.setFocus("")}
          onClearWindow={onClearWindow}
        />
        <PeerBand peer={feeds.snap.snapshot?.peer ?? null} />
        <FleetBand state={mirror} nowMs={feeds.nowMs} />
        {fleetNotice !== null && (
          <p className="fleet-notice" role="status">
            {fleetNotice}{" "}
            <button type="button" className="fleet-notice__close" onClick={() => setFleetNotice(null)}>
              {t("scope.fleetNoticeClose")}
            </button>
          </p>
        )}
        {!fleetScope && (
          <PanelBoundary name="Activity spine">
            <ActivitySpine
              key={feeds.provenance === "hub" ? "hub" : "derived"}
              source={feeds.spineSource}
              onBrush={setBrush}
              brush={brush}
              workspaceSelection={workspace.selection}
            />
          </PanelBoundary>
        )}
        {!observed && (
          <>
            <PanelBoundary name="Federation">
              <FederationRow
                state={feeds.federation}
                hubVersion={feeds.snap.snapshot?.hub_version ?? ""}
                configEpoch={feeds.snap.snapshot?.config_epoch ?? ""}
              />
            </PanelBoundary>
            <ReplayWorkbench
              replay={workspace.replay}
              slotA={replay.slotA}
              slotB={replay.slotB}
              events={feeds.log}
              onReplayChange={setReplay}
              onReplayReplace={replaceReplay}
              onSelectEvent={(sequence) =>
                setPanelSelection("log", { kind: "event", seq: sequence })
              }
              onSelectTask={(taskId) =>
                setPanelSelection("causality", { kind: "task", id: taskId })
              }
            />
          </>
        )}
        <MobileNav active={mobileSegment} onSelect={setMobileSegment} />
        <div className={`deck deck--seg-${mobileSegment}`} role="main">
          <div className="deck__stack deck__stack--roster">
            <div className="seg seg--roster">
              <div className="roster-workspace">
                <nav className="roster-workspace__switch" aria-label={t("host.switch")}>
                  <button type="button" aria-pressed={!hostSessionsOpen} onClick={() => setHostSessionsOpen(false)}>{t("host.roster")}</button>
                  <button type="button" aria-pressed={hostSessionsOpen} onClick={() => setHostSessionsOpen(true)}>{t("host.title")}</button>
                </nav>
                {hostSessionsOpen ? <PanelBoundary name={t("host.title")}>
                  {!observed ? (
                    <HostSessions revision={auth.revision} />
                  ) : (
                    observedPanel(t("host.title"), t("host.title"))
                  )}
                </PanelBoundary> : <PanelBoundary name="Fleet roster">
                {fleetScope ? (
                  <FleetFeedPanel label="Fleet roster" title={t("host.roster")} />
                ) : (
                  <FleetRoster
                    roster={view.roster}
                    waiters={view.waiters}
                    presenceHolders={view.presenceHolders}
                    selection={workspace.selection}
                    onInspect={overlays.inspectAgent}
                  />
                )}
                </PanelBoundary>}
              </div>
            </div>
            <div className="seg seg--reliability">
              <PanelBoundary name="Reliability">
                {!observed ? (
                  <ReliabilityPanel state={feeds.reliability} />
                ) : (
                  observedPanel("Reliability evidence", "Reliability")
                )}
              </PanelBoundary>
            </div>
          </div>
          <div className="deck__stack">
            <div className="seg seg--claims">
              <PanelBoundary name="Claims">
                {fleetScope ? (
                  <FleetClaimsPanel state={mirror} />
                ) : (
                  <ClaimsBoard
                    claims={view.lensedClaims}
                    conflicts={view.conflicts}
                    connected={view.connected}
                    lens={preferences.focus}
                    selection={workspace.selection}
                  />
                )}
              </PanelBoundary>
            </div>
            <div className="seg seg--signals">
              <PanelBoundary name="Inspector">
                <InspectorTabs
                  tab={workspace.panel}
                  onTabChange={setPanel}
                  fleetView={workspace.fleetView}
                  onFleetViewChange={setFleetView}
                  fleetSelection={fleetSelectionOf(workspace.selection)}
                  onFleetSelectionChange={setFleetSelection}
                  communicationFilter={{
                    query: workspace.communicationQuery,
                    health: workspace.communicationHealth,
                  }}
                  onCommunicationFilterChange={setCommunicationFilter}
                  selection={workspace.selection}
                  onSelectionChange={(selection) => {
                    if (selection?.kind === "task") {
                      setPanelSelection("causality", selection);
                    } else {
                      setSelection(selection);
                    }
                  }}
                  attention={view.attention}
                  attentionFeed={feeds.attention}
                  onInspectAgent={overlays.inspectAgent}
                  onInspectTask={overlays.inspectTask}
                  operation={workspace.operation ?? null}
                  onOperationChange={setOperation}
                  canDecideApproval={capabilities.task_update && !observed}
                  events={feeds.log}
                  window={brush}
                  onClearWindow={onClearWindow}
                  provenance={feeds.provenance === "hub" ? "hub" : "derived"}
                  coverage={feeds.coverage}
                  query={preferences.logQuery}
                  onQueryChange={preferences.setLogQuery}
                  claims={view.claims}
                  conflicts={view.conflicts}
                  liveAgentCount={feeds.snap.snapshot?.fleet.agents.live.length ?? 0}
                  agents={view.roster.map((entry) => entry.agent)}
                  canMessagePeer={capabilities.message_send && !observed}
                  onMessagePeer={overlays.messagePeer}
                  connected={view.connected}
                  federation={feeds.federation}
                  metrics={feeds.metrics}
                  sessions={feeds.sessions}
                  receipts={feeds.receipts}
                  operatorActions={feeds.operatorActions}
                  onOpenEvent={(sequence) =>
                    setPanelSelection("log", { kind: "event", seq: sequence })
                  }
                  traceRequest={overlays.traceRequest}
                  incidentStep={workspace.incidentStep}
                  onIncidentStepChange={setIncidentStep}
                  incidentStorageKey={`synapse-cockpit-incident-v1:${access.descriptor?.principal ?? "unavailable"}`}
                  replay={workspace.replay}
                  hubVersion={feeds.snap.snapshot?.hub_version ?? ""}
                  configEpoch={feeds.snap.snapshot?.config_epoch ?? ""}
                  onOpenIncidentEvidence={(selection) => {
                    if (selection.kind === "task") {
                      setPanelSelection("causality", selection);
                    } else if (selection.kind === "event") {
                      setPanelSelection("log", selection);
                    } else {
                      setPanelSelection("fleet", selection);
                    }
                  }}
                />
              </PanelBoundary>
            </div>
          </div>
          <div className="seg seg--board">
            <PanelBoundary name="Board">
              {fleetScope ? (
                <FleetBoardPanel state={mirror} />
              ) : (
              <TaskBoard
                tasks={view.lensedBoard}
                connected={view.connected}
                truncation={
                  replay.travelling ? undefined : boardTruncation(feeds.snap.snapshot)
                }
                onInspect={overlays.inspectTask}
                lens={preferences.focus}
                selection={workspace.selection}
              />
              )}
            </PanelBoundary>
          </div>
          <div className="deck__stack deck__stack--rail">
            <div className="seg seg--signals">
              <PanelBoundary name="Risk rail">
                {fleetScope ? (
                  <FleetFeedPanel label="Risk rail" title="Risk" />
                ) : (
                <RiskRail
                  risk={feeds.snap.snapshot?.risk ?? null}
                  anomalies={view.anomalies}
                  deadLetters={view.deadLetters}
                  waits={feeds.waits}
                  anomalyReport={feeds.anomalyReport}
                  approvals={view.approvals}
                  selection={workspace.selection}
                />
                )}
              </PanelBoundary>
            </div>
            <div className="seg seg--signals">
              <PanelBoundary name="Findings">
                {!observed ? (
                  <FindingsStream state={feeds.findings} />
                ) : (
                  observedPanel("Findings stream", "Findings")
                )}
              </PanelBoundary>
            </div>
          </div>
        </div>
        <InstallChip />
        <Palette
          open={overlays.paletteOpen}
          commands={overlays.commands}
          compose={overlays.paletteCompose}
          onClose={overlays.closePalette}
          onRun={overlays.runCommand}
        />
        <GuideDrawer
          open={overlays.guideOpen}
          activePanel={workspace.panel}
          onClose={overlays.closeGuide}
          onOpenSetup={overlays.openSetup}
        />
        <SetupAssistant
          open={overlays.setupOpen}
          onClose={overlays.closeSetup}
          canApplySetup={capabilities.setup_plan && capabilities.setup_apply && peerHub === null}
          evidence={{
            access: access.phase,
            snapshot: feeds.snap.status,
            transport: feeds.transport.status,
            optionalFeeds: [
              feeds.reliability.status,
              feeds.federation.status,
              feeds.metrics.status,
              feeds.sessions.status,
              feeds.waits.status,
              feeds.anomalyReport.status,
              feeds.receipts.status,
              feeds.operatorActions.status,
            ],
            served: served.data,
            loopbackOrigin: isLoopbackHostname(location.hostname),
          }}
        />
        <ToastStack
          toasts={toastController.toasts}
          onDismiss={toastController.dismiss}
        />
        <DetailDrawer
          agent={
            overlays.inspected?.kind === "agent" &&
            workspace.selection?.kind === "agent" &&
            workspace.selection.id === overlays.inspected.id
              ? agentDetail(
                overlays.inspected.id,
                view.roster,
                view.claims,
                view.deadLetters,
                feeds.log,
              )
              : undefined
          }
          task={
            overlays.inspected?.kind === "task" &&
            workspace.selection?.kind === "task" &&
            workspace.selection.id === overlays.inspected.id
              ? taskDetail(
                overlays.inspected.id,
                view.board,
                view.claims,
                feeds.log,
              )
              : undefined
          }
          onClose={overlays.closeInspection}
          onFilterLog={(text) => {
            preferences.setLogQuery({
              text,
              kinds: null,
              order: "newest",
              view: "flat",
            });
            overlays.closeInspection();
          }}
          onTrace={(taskId) => {
            overlays.requestTrace(taskId);
            overlays.closeInspection();
          }}
        />
      </div>
    </ScopeProvider>
  );
}
