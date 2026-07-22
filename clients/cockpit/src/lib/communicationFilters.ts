// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — deterministic communication-model filtering

import type {
  CommunicationEdge,
  CommunicationModel,
  CommunicationNode,
  ProjectTraffic,
} from "./communications";

export const COMMUNICATION_HEALTH_FILTERS = [
  "all",
  "healthy",
  "deferred",
  "failed",
  "unknown",
] as const;
export type CommunicationHealthFilter = (typeof COMMUNICATION_HEALTH_FILTERS)[number];

export const COMMUNICATION_QUERY_LIMIT = 120;

export interface CommunicationFilter {
  readonly query: string;
  readonly health: CommunicationHealthFilter;
}

export const DEFAULT_COMMUNICATION_FILTER: CommunicationFilter = Object.freeze({
  query: "",
  health: "all",
});

/** Narrow an untrusted control value to a supported delivery-health filter. */
export function communicationHealthFilter(value: string): CommunicationHealthFilter {
  return COMMUNICATION_HEALTH_FILTERS.find((candidate) => candidate === value) ?? "all";
}

function queryMatches(query: string, identity: string): boolean {
  // A scoped project is the identity prefix, so one identity match covers
  // both exact names and project names without a redundant second branch.
  return identity.toLowerCase().includes(query);
}

/** Trim and bound an operator communication-filter query. */
export function normaliseCommunicationQuery(raw: string): string {
  return raw.trim().slice(0, COMMUNICATION_QUERY_LIMIT);
}

/** Whether a communication filter changes the full-window projection. */
export function communicationFilterIsActive(filter: CommunicationFilter): boolean {
  return normaliseCommunicationQuery(filter.query) !== "" || filter.health !== "all";
}

function emptyNode(node: CommunicationNode): CommunicationNode {
  return {
    ...node,
    messages: 0,
    inbound: 0,
    outbound: 0,
    delivered: 0,
    deferred: 0,
    failed: 0,
  };
}

function applyEdge(node: CommunicationNode, edge: CommunicationEdge, inbound: boolean): CommunicationNode {
  return {
    ...node,
    messages: node.messages + edge.messages,
    inbound: node.inbound + (inbound ? edge.messages : 0),
    outbound: node.outbound + (inbound ? 0 : edge.messages),
    delivered: node.delivered + edge.delivered,
    deferred: node.deferred + edge.deferred,
    failed: node.failed + edge.failed,
    lastTs: Math.max(node.lastTs, edge.lastTs),
  };
}

function filteredNodes(
  model: CommunicationModel,
  edges: readonly CommunicationEdge[],
  query: string,
  health: CommunicationHealthFilter,
): CommunicationNode[] {
  const ids = new Set<string>();
  for (const edge of edges) {
    ids.add(edge.source);
    ids.add(edge.target);
  }
  if (health === "all" && query !== "") {
    for (const node of model.nodes) if (queryMatches(query, node.id)) ids.add(node.id);
  }
  const nodes = new Map(
    model.nodes.filter((node) => ids.has(node.id)).map((node) => [node.id, emptyNode(node)]),
  );
  for (const edge of edges) {
    const source = nodes.get(edge.source);
    const target = nodes.get(edge.target);
    if (edge.source === edge.target && source !== undefined) {
      nodes.set(edge.source, applyEdge(applyEdge(source, edge, false), edge, true));
      continue;
    }
    if (source !== undefined) nodes.set(edge.source, applyEdge(source, edge, false));
    if (target !== undefined) nodes.set(edge.target, applyEdge(target, edge, true));
  }
  return [...nodes.values()].sort(
    (left, right) => right.messages - left.messages || right.lastTs - left.lastTs || left.id.localeCompare(right.id),
  );
}

function filteredProjects(model: CommunicationModel, nodes: readonly CommunicationNode[]): ProjectTraffic[] {
  const claimCounts = new Map(model.projects.map((project) => [project.id, project.claims]));
  const projects = new Map<string, ProjectTraffic>();
  for (const node of nodes) {
    const current = projects.get(node.project);
    projects.set(node.project, {
      id: node.project,
      members: current === undefined ? [node.id] : [...current.members, node.id],
      inbound: (current?.inbound ?? 0) + node.inbound,
      outbound: (current?.outbound ?? 0) + node.outbound,
      claims: claimCounts.get(node.project) ?? 0,
      lastTs: Math.max(current?.lastTs ?? 0, node.lastTs),
    });
  }
  return [...projects.values()]
    .map((project) => ({ ...project, members: [...project.members].sort((a, b) => a.localeCompare(b)) }))
    .sort(
      (left, right) =>
        right.inbound + right.outbound - (left.inbound + left.outbound) ||
        right.lastTs - left.lastTs ||
        left.id.localeCompare(right.id),
    );
}

/** Filter a communication model without inventing edges or receipt outcomes. */
export function filterCommunicationModel(
  model: CommunicationModel,
  filter: CommunicationFilter,
): CommunicationModel {
  const query = normaliseCommunicationQuery(filter.query).toLowerCase();
  if (query === "" && filter.health === "all") return model;
  const edges = model.edges.filter((edge) =>
    (filter.health === "all" || edge.health === filter.health) &&
    (query === "" || queryMatches(query, edge.source) || queryMatches(query, edge.target)),
  );
  const nodes = filteredNodes(model, edges, query, filter.health);
  return {
    nodes,
    edges,
    projects: filteredProjects(model, nodes),
    messages: edges.reduce((total, edge) => total + edge.messages, 0),
  };
}
