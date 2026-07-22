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
import { FleetRoster } from "./components/FleetRoster";
import { GuideDrawer } from "./components/GuideDrawer";
import { Hud } from "./components/Hud";
import { InspectorTabs } from "./components/InspectorTabs";
import { InstallChip } from "./components/InstallChip";
import { MobileNav, type MobileSegment } from "./components/MobileNav";
import { Palette } from "./components/Palette";
import { PanelBoundary } from "./components/PanelBoundary";
import { ReliabilityPanel } from "./components/ReliabilityPanel";
import { ReplayWorkbench } from "./components/ReplayWorkbench";
import { RiskRail } from "./components/RiskRail";
import { RoleBadge } from "./components/RoleBadge";
import { SelectionBar } from "./components/SelectionBar";
import { SetupAssistant } from "./components/SetupAssistant";
import { TaskBoard } from "./components/TaskBoard";
import { ToastStack } from "./components/ToastStack";
import { useCockpitI18n } from "./context/CockpitI18n";
import { useCockpitFeeds } from "./hooks/useCockpitFeeds";
import { useCockpitOverlays } from "./hooks/useCockpitOverlays";
import { useCockpitPreferences } from "./hooks/useCockpitPreferences";
import { useCockpitReplay } from "./hooks/useCockpitReplay";
import { useCockpitToasts } from "./hooks/useCockpitToasts";
import { useCockpitViewModel } from "./hooks/useCockpitViewModel";
import { useCockpitWorkspace } from "./hooks/useCockpitWorkspace";
import { useDashboardAccess } from "./hooks/useDashboardAccess";
import { capabilitiesOf } from "./lib/access";
import { boardTruncation } from "./lib/board";
import type { TimeWindow } from "./lib/brush";
import { agentDetail, taskDetail } from "./lib/detail";
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
  } = useCockpitWorkspace();
  const auth = useSyncExternalStore(
    subscribeCockpitAuth,
    cockpitAuthSnapshot,
    cockpitAuthSnapshot,
  );
  const authBlocked = auth.phase === "locked";
  const access = useDashboardAccess(authBlocked, auth.revision);
  const shellBlocked = authBlocked || access.phase === "loading";
  const feeds = useCockpitFeeds(shellBlocked, auth.revision);
  const preferences = useCockpitPreferences();
  const [brush, setBrush] = useState<TimeWindow | null>(null);
  const [mobileSegment, setMobileSegment] = useState<MobileSegment>("signals");
  const replay = useCockpitReplay({
    blocked: shellBlocked,
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
    accessChangedMessage: t("app.accessChanged"),
    agents: view.roster.map((entry) => entry.agent),
    tasks: view.liveBoard.map((task) => task.taskId),
    setSelection,
    setFocus: preferences.setFocus,
    toggleTheme: preferences.toggleTheme,
    toggleDensity: preferences.toggleDensity,
    toggleTravel: replay.toggleTravel,
  });
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

  if (authBlocked) {
    return <AuthVeil reason={auth.reason} onUnlock={unlockCockpit} />;
  }
  if (access.phase === "loading") {
    return <main className="access-probe" role="status">{t("app.accessChecking")}</main>;
  }

  return (
    <div className="shell">
      <span className="visually-hidden" role="status" aria-live="polite">
        {overlays.accessNotice}
      </span>
      <Hud
        kpis={feeds.kpis}
        live={feeds.snap.status === "live"}
        stamp={feeds.stamp}
        transport={feeds.transport.status}
        onSelect={onSelectKpi}
        theme={preferences.theme}
        onToggleTheme={preferences.toggleTheme}
        focus={preferences.focus}
        onFocusChange={preferences.setFocus}
        rosterNames={view.roster.map((entry) => entry.agent)}
        density={preferences.density}
        onToggleDensity={preferences.toggleDensity}
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
      <PanelBoundary name="Activity spine">
        <ActivitySpine
          key={feeds.provenance === "hub" ? "hub" : "derived"}
          source={feeds.spineSource}
          onBrush={setBrush}
          brush={brush}
          workspaceSelection={workspace.selection}
        />
      </PanelBoundary>
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
      <MobileNav active={mobileSegment} onSelect={setMobileSegment} />
      <div className={`deck deck--seg-${mobileSegment}`} role="main">
        <div className="deck__stack deck__stack--roster">
          <div className="seg seg--roster">
            <PanelBoundary name="Fleet roster">
              <FleetRoster
                roster={view.roster}
                waiters={view.waiters}
                selection={workspace.selection}
                onInspect={overlays.inspectAgent}
              />
            </PanelBoundary>
          </div>
          <div className="seg seg--reliability">
            <PanelBoundary name="Reliability">
              <ReliabilityPanel state={feeds.reliability} />
            </PanelBoundary>
          </div>
        </div>
        <div className="deck__stack">
          <div className="seg seg--claims">
            <PanelBoundary name="Claims">
              <ClaimsBoard
                claims={view.lensedClaims}
                conflicts={view.conflicts}
                connected={view.connected}
                lens={preferences.focus}
                selection={workspace.selection}
              />
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
                onInspectAgent={overlays.inspectAgent}
                onInspectTask={overlays.inspectTask}
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
                canMessagePeer={capabilities.message_send}
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
          </PanelBoundary>
        </div>
        <div className="deck__stack deck__stack--rail">
          <div className="seg seg--signals">
            <PanelBoundary name="Risk rail">
              <RiskRail
                risk={feeds.snap.snapshot?.risk ?? null}
                anomalies={view.anomalies}
                deadLetters={view.deadLetters}
                waits={feeds.waits}
                anomalyReport={feeds.anomalyReport}
                approvals={view.approvals}
                selection={workspace.selection}
              />
            </PanelBoundary>
          </div>
          <div className="seg seg--signals">
            <PanelBoundary name="Findings">
              <FindingsStream
                findings={view.findings}
                connected={view.connected}
              />
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
  );
}
