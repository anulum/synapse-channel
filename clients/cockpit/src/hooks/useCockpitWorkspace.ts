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
  type FleetSelection,
  type FleetView,
  type InspectorTab,
} from "../lib/workspace";

interface CockpitWorkspaceController {
  readonly workspace: CockpitWorkspace;
  readonly setPanel: (panel: InspectorTab) => void;
  readonly setFleetView: (view: FleetView) => void;
  readonly setFleetSelection: (selection: FleetSelection | null) => void;
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

  const navigate = useCallback((change: (current: CockpitWorkspace) => CockpitWorkspace) => {
    const next = change(workspaceRef.current);
    const search = workspaceToSearch(next, location.search);
    if (search === location.search) return;
    const url = `${location.pathname}${search}${location.hash}`;
    history.pushState(history.state, "", url);
    workspaceRef.current = next;
    setWorkspace(next);
  }, []);

  const setPanel = useCallback(
    (panel: InspectorTab) =>
      navigate((current) => ({
        ...current,
        panel,
        selection: panel === "fleet" ? current.selection : null,
      })),
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

  return { workspace, setPanel, setFleetView, setFleetSelection };
}
