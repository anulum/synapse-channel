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

export type CockpitSelection =
  | { readonly kind: "agent"; readonly id: string }
  | { readonly kind: "project"; readonly id: string }
  | { readonly kind: "task"; readonly id: string }
  | { readonly kind: "route"; readonly source: string; readonly target: string }
  | { readonly kind: "event"; readonly seq: number };

export type FleetSelection = Extract<
  CockpitSelection,
  { readonly kind: "agent" | "project" | "route" }
>;

export interface CockpitWorkspace {
  readonly panel: InspectorTab;
  readonly fleetView: FleetView;
  readonly selection: CockpitSelection | null;
}

export const DEFAULT_WORKSPACE: CockpitWorkspace = {
  panel: "log",
  fleetView: "web",
  selection: null,
};

const WORKSPACE_PARAMS = [
  "panel",
  "fleet",
  "agent",
  "project",
  "task",
  "event",
  "from",
  "to",
] as const;
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

function eventSequence(params: URLSearchParams): number | null {
  const raw = params.get("event");
  if (raw === null || !/^(0|[1-9]\d*)$/u.test(raw)) return null;
  const seq = Number(raw);
  return Number.isSafeInteger(seq) ? seq : null;
}

function selectionFromParams(params: URLSearchParams): CockpitSelection | null {
  const source = entity(params, "from");
  const target = entity(params, "to");
  if (source !== null && target !== null) return { kind: "route", source, target };
  const event = eventSequence(params);
  if (event !== null) return { kind: "event", seq: event };
  const agent = entity(params, "agent");
  if (agent !== null) return { kind: "agent", id: agent };
  const task = entity(params, "task");
  if (task !== null) return { kind: "task", id: task };
  const project = entity(params, "project");
  return project === null ? null : { kind: "project", id: project };
}

export function workspaceFromSearch(search: string): CockpitWorkspace {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const panelCandidate = params.get("panel");
  const fleetCandidate = params.get("fleet");
  const panel = memberOf(INSPECTOR_TABS, panelCandidate) ? panelCandidate : DEFAULT_WORKSPACE.panel;
  const fleetView = memberOf(FLEET_VIEWS, fleetCandidate) ? fleetCandidate : DEFAULT_WORKSPACE.fleetView;
  return { panel, fleetView, selection: selectionFromParams(params) };
}

export function workspaceToSearch(workspace: CockpitWorkspace, currentSearch = ""): string {
  const params = new URLSearchParams(currentSearch.startsWith("?") ? currentSearch.slice(1) : currentSearch);
  for (const key of WORKSPACE_PARAMS) params.delete(key);

  if (workspace.panel !== DEFAULT_WORKSPACE.panel) params.set("panel", workspace.panel);
  if (workspace.panel === "fleet" && workspace.fleetView !== DEFAULT_WORKSPACE.fleetView) {
    params.set("fleet", workspace.fleetView);
  }
  if (workspace.selection !== null) {
    if (workspace.selection.kind === "route") {
      params.set("from", workspace.selection.source);
      params.set("to", workspace.selection.target);
    } else if (workspace.selection.kind === "event") {
      params.set("event", String(workspace.selection.seq));
    } else {
      params.set(workspace.selection.kind, workspace.selection.id);
    }
  }

  const query = params.toString();
  return query === "" ? "" : `?${query}`;
}
