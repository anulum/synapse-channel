// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — URL-addressable cockpit workspace state

export const INSPECTOR_TABS = [
  "attention",
  "log",
  "fleet",
  "topology",
  "metrics",
  "audit",
  "causality",
] as const;
export type InspectorTab = (typeof INSPECTOR_TABS)[number];

export const FLEET_VIEWS = ["web", "matrix", "projects"] as const;
export type FleetView = (typeof FLEET_VIEWS)[number];

export type FleetSelection =
  | { readonly kind: "agent" | "project"; readonly id: string }
  | { readonly kind: "route"; readonly source: string; readonly target: string };

export interface CockpitWorkspace {
  readonly panel: InspectorTab;
  readonly fleetView: FleetView;
  readonly selection: FleetSelection | null;
}

export const DEFAULT_WORKSPACE: CockpitWorkspace = {
  panel: "log",
  fleetView: "web",
  selection: null,
};

const WORKSPACE_PARAMS = ["panel", "fleet", "agent", "project", "from", "to"] as const;
const ENTITY_MAX_LENGTH = 512;
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f]/u;

function memberOf<const Values extends readonly string[]>(
  values: Values,
  candidate: string | null,
): candidate is Values[number] {
  return candidate !== null && values.includes(candidate);
}

function entity(params: URLSearchParams, key: string): string | null {
  const raw = params.get(key) ?? "";
  const value = raw.trim();
  if (value === "" || value.length > ENTITY_MAX_LENGTH || CONTROL_CHARACTERS.test(raw)) return null;
  return value;
}

function selectionFromParams(params: URLSearchParams, panel: InspectorTab): FleetSelection | null {
  if (panel !== "fleet") return null;
  const source = entity(params, "from");
  const target = entity(params, "to");
  if (source !== null && target !== null) return { kind: "route", source, target };
  const agent = entity(params, "agent");
  if (agent !== null) return { kind: "agent", id: agent };
  const project = entity(params, "project");
  return project === null ? null : { kind: "project", id: project };
}

export function workspaceFromSearch(search: string): CockpitWorkspace {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const panelCandidate = params.get("panel");
  const fleetCandidate = params.get("fleet");
  const panel = memberOf(INSPECTOR_TABS, panelCandidate) ? panelCandidate : DEFAULT_WORKSPACE.panel;
  const fleetView = memberOf(FLEET_VIEWS, fleetCandidate) ? fleetCandidate : DEFAULT_WORKSPACE.fleetView;
  return { panel, fleetView, selection: selectionFromParams(params, panel) };
}

export function workspaceToSearch(workspace: CockpitWorkspace, currentSearch = ""): string {
  const params = new URLSearchParams(currentSearch.startsWith("?") ? currentSearch.slice(1) : currentSearch);
  for (const key of WORKSPACE_PARAMS) params.delete(key);

  if (workspace.panel !== DEFAULT_WORKSPACE.panel) params.set("panel", workspace.panel);
  if (workspace.panel === "fleet" && workspace.fleetView !== DEFAULT_WORKSPACE.fleetView) {
    params.set("fleet", workspace.fleetView);
  }
  if (workspace.panel === "fleet" && workspace.selection !== null) {
    if (workspace.selection.kind === "route") {
      params.set("from", workspace.selection.source);
      params.set("to", workspace.selection.target);
    } else {
      params.set(workspace.selection.kind, workspace.selection.id);
    }
  }

  const query = params.toString();
  return query === "" ? "" : `?${query}`;
}
