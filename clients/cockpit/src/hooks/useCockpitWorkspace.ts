// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — browser-history binding for cockpit workspace state

import { useCallback, useEffect, useRef, useState } from "react";

import {
  workspaceFromSearch,
  workspaceToSearch,
  type CockpitWorkspace,
  type CockpitSelection,
  type FleetSelection,
  type FleetView,
  type IncidentStep,
  type InspectorTab,
  type ReplayState,
} from "../lib/workspace";
import type { CommunicationFilter } from "../lib/communicationFilters";

interface CockpitWorkspaceController {
  readonly workspace: CockpitWorkspace;
  readonly setPanel: (panel: InspectorTab) => void;
  readonly setFleetView: (view: FleetView) => void;
  readonly setSelection: (selection: CockpitSelection | null) => void;
  readonly setPanelSelection: (panel: InspectorTab, selection: CockpitSelection | null) => void;
  readonly setFleetSelection: (selection: FleetSelection | null) => void;
  readonly setReplay: (replay: ReplayState) => void;
  readonly replaceReplay: (replay: ReplayState) => void;
  readonly setIncidentStep: (step: IncidentStep) => void;
  readonly setCommunicationFilter: (filter: CommunicationFilter) => void;
}

function currentWorkspace(): CockpitWorkspace {
  return workspaceFromSearch(typeof location === "undefined" ? "" : location.search);
}

export function useCockpitWorkspace(): CockpitWorkspaceController {
  const [workspace, setWorkspace] = useState<CockpitWorkspace>(currentWorkspace);
  const workspaceRef = useRef(workspace);

  useEffect(() => {
    const restore = (): void => {
      const restored = currentWorkspace();
      workspaceRef.current = restored;
      setWorkspace(restored);
    };
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);

  const navigate = useCallback((
    change: (current: CockpitWorkspace) => CockpitWorkspace,
    replace = false,
  ) => {
    const next = change(workspaceRef.current);
    const search = workspaceToSearch(next, location.search);
    if (search === location.search) return;
    const url = `${location.pathname}${search}${location.hash}`;
    if (replace) history.replaceState(history.state, "", url);
    else history.pushState(history.state, "", url);
    workspaceRef.current = next;
    setWorkspace(next);
  }, []);

  const setPanel = useCallback(
    (panel: InspectorTab) => navigate((current) => ({ ...current, panel })),
    [navigate],
  );

  const setFleetView = useCallback(
    (fleetView: FleetView) => navigate((current) => ({ ...current, panel: "fleet", fleetView })),
    [navigate],
  );

  const setFleetSelection = useCallback(
    (selection: FleetSelection | null) =>
      navigate((current) => ({ ...current, panel: "fleet", selection })),
    [navigate],
  );

  const setSelection = useCallback(
    (selection: CockpitSelection | null) => navigate((current) => ({ ...current, selection })),
    [navigate],
  );

  const setPanelSelection = useCallback(
    (panel: InspectorTab, selection: CockpitSelection | null) =>
      navigate((current) => ({ ...current, panel, selection })),
    [navigate],
  );

  const setReplay = useCallback(
    (replay: ReplayState) => navigate((current) => ({ ...current, replay })),
    [navigate],
  );

  const replaceReplay = useCallback(
    (replay: ReplayState) => navigate((current) => ({ ...current, replay }), true),
    [navigate],
  );

  const setIncidentStep = useCallback(
    (incidentStep: IncidentStep) => navigate((current) => ({
      ...current,
      panel: "incident",
      incidentStep,
    })),
    [navigate],
  );

  const setCommunicationFilter = useCallback(
    (filter: CommunicationFilter) => navigate((current) => ({
      ...current,
      panel: "fleet",
      communicationQuery: filter.query,
      communicationHealth: filter.health,
    }), true),
    [navigate],
  );

  return {
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
  };
}
