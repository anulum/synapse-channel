// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — metadata-only fleet communication projection

import type { CockpitEvent } from "../types";
import { eventsInWindow, type TimeWindow } from "./brush";
import type { ClaimView } from "./claims";
import {
  communicationText,
  finiteCommunicationNumber,
  isChatEvent,
  receiptOutcome,
  receiptOutcomeRank,
  type ReceiptOutcome,
} from "./communicationEvidence";

/** Delivery state derived from retained receipt evidence. */
export type DeliveryHealth = "healthy" | "deferred" | "failed" | "unknown";

/** One identity in the metadata-only communication projection. */
export interface CommunicationNode {
  readonly id: string;
  readonly project: string;
  readonly exact: boolean;
  readonly messages: number;
  readonly inbound: number;
  readonly outbound: number;
  readonly delivered: number;
  readonly deferred: number;
  readonly failed: number;
  readonly lastTs: number;
}

/** One directed route in the metadata-only communication projection. */
export interface CommunicationEdge {
  readonly id: string;
  readonly source: string;
  readonly target: string;
  readonly messages: number;
  readonly delivered: number;
  readonly deferred: number;
  readonly failed: number;
  readonly lastTs: number;
  readonly health: DeliveryHealth;
}

/** Project-level traffic and live-claim summary. */
export interface ProjectTraffic {
  readonly id: string;
  readonly members: readonly string[];
  readonly inbound: number;
  readonly outbound: number;
  readonly claims: number;
  readonly lastTs: number;
}

/** Bounded communication metadata used by every fleet view. */
export interface CommunicationModel {
  readonly nodes: readonly CommunicationNode[];
  readonly edges: readonly CommunicationEdge[];
  readonly projects: readonly ProjectTraffic[];
  readonly messages: number;
}

interface MutableNode {
  id: string;
  project: string;
  exact: boolean;
  messages: number;
  inbound: number;
  outbound: number;
  delivered: number;
  deferred: number;
  failed: number;
  lastTs: number;
}

interface MutableEdge {
  id: string;
  source: string;
  target: string;
  messages: number;
  delivered: number;
  deferred: number;
  failed: number;
  lastTs: number;
}

/** Derive the stable project segment for one routing identity. */
export function projectOf(identity: string): string {
  const slash = identity.indexOf("/");
  if (slash > 0) return identity.slice(0, slash);
  if (identity === "all" || identity === "CEO") return "fleet-wide";
  return /^[A-Z][A-Z0-9-]+$/u.test(identity) ? identity : "unscoped";
}

function isExactIdentity(identity: string): boolean {
  return identity.includes("/") && !identity.includes("*") && !identity.includes("?");
}

function edgeHealth(edge: MutableEdge): DeliveryHealth {
  if (edge.failed > 0) return "failed";
  if (edge.deferred > 0) return "deferred";
  if (edge.delivered > 0) return "healthy";
  return "unknown";
}

function ensureCommunicationNode(nodes: Map<string, MutableNode>, id: string): MutableNode {
  const existing = nodes.get(id);
  if (existing !== undefined) return existing;
  const created: MutableNode = {
    id,
    project: projectOf(id),
    exact: isExactIdentity(id),
    messages: 0,
    inbound: 0,
    outbound: 0,
    delivered: 0,
    deferred: 0,
    failed: 0,
    lastTs: 0,
  };
  nodes.set(id, created);
  return created;
}

function addChatEvent(
  event: CockpitEvent,
  nodes: Map<string, MutableNode>,
  edges: Map<string, MutableEdge>,
  messageEdges: Map<number, string>,
): void {
  const source = communicationText(event.payload?.["sender"]);
  const target = communicationText(event.payload?.["target"]);
  if (source === "" || target === "") return;
  const sourceNode = ensureCommunicationNode(nodes, source);
  const targetNode = ensureCommunicationNode(nodes, target);
  const id = `${source}\u0000${target}`;
  const edge = edges.get(id) ?? {
    id,
    source,
    target,
    messages: 0,
    delivered: 0,
    deferred: 0,
    failed: 0,
    lastTs: 0,
  };
  edge.messages += 1;
  edge.lastTs = Math.max(edge.lastTs, event.ts);
  edges.set(id, edge);
  sourceNode.outbound += 1;
  sourceNode.messages += 1;
  sourceNode.lastTs = Math.max(sourceNode.lastTs, event.ts);
  targetNode.inbound += 1;
  targetNode.messages += 1;
  targetNode.lastTs = Math.max(targetNode.lastTs, event.ts);
  messageEdges.set(event.seq, id);
}

function collectReceiptOutcomes(
  event: CockpitEvent,
  outcomes: Map<number, ReceiptOutcome>,
): void {
  const outcome = receiptOutcome(event);
  const messageSeq = finiteCommunicationNumber(event.payload?.["message_seq"]);
  if (outcome === null || messageSeq === null) return;
  const prior = outcomes.get(messageSeq);
  if (prior === undefined || receiptOutcomeRank(outcome) >= receiptOutcomeRank(prior)) {
    outcomes.set(messageSeq, outcome);
  }
}

function applyReceiptOutcomes(
  outcomes: ReadonlyMap<number, ReceiptOutcome>,
  messageEdges: ReadonlyMap<number, string>,
  edges: Map<string, MutableEdge>,
  nodes: Map<string, MutableNode>,
): void {
  for (const [messageSeq, outcome] of outcomes) {
    const edgeId = messageEdges.get(messageSeq);
    if (edgeId === undefined) continue;
    const edge = edges.get(edgeId)!;
    edge[outcome] += 1;
    nodes.get(edge.source)![outcome] += 1;
    nodes.get(edge.target)![outcome] += 1;
  }
}

function projectTraffic(
  nodes: ReadonlyMap<string, MutableNode>,
  claims: readonly ClaimView[],
): ProjectTraffic[] {
  const claimCount = new Map<string, number>();
  for (const claim of claims) {
    const project = projectOf(claim.claim.owner);
    claimCount.set(project, (claimCount.get(project) ?? 0) + 1);
  }
  const projects = new Map<
    string,
    { members: Set<string>; inbound: number; outbound: number; lastTs: number }
  >();
  for (const node of nodes.values()) {
    const project = projects.get(node.project) ?? {
      members: new Set<string>(),
      inbound: 0,
      outbound: 0,
      lastTs: 0,
    };
    project.members.add(node.id);
    project.inbound += node.inbound;
    project.outbound += node.outbound;
    project.lastTs = Math.max(project.lastTs, node.lastTs);
    projects.set(node.project, project);
  }
  return [...projects.entries()]
    .map(([id, project]) => ({
      id,
      members: [...project.members].sort((left, right) => left.localeCompare(right)),
      inbound: project.inbound,
      outbound: project.outbound,
      claims: claimCount.get(id) ?? 0,
      lastTs: project.lastTs,
    }))
    .sort(
      (left, right) =>
        right.inbound + right.outbound - (left.inbound + left.outbound) ||
        right.lastTs - left.lastTs ||
        left.id.localeCompare(right.id),
    );
}

/**
 * Derive a bounded metadata-only communication model. Message bodies never
 * enter the result; receipts correlate by the hub-attested message sequence.
 */
export function deriveCommunicationModel(
  events: readonly CockpitEvent[],
  claims: readonly ClaimView[] = [],
  agents: readonly string[] = [],
  window: TimeWindow | null = null,
): CommunicationModel {
  const scoped = eventsInWindow(events, window);
  const nodes = new Map<string, MutableNode>();
  const edges = new Map<string, MutableEdge>();
  const messageEdges = new Map<number, string>();
  const outcomes = new Map<number, ReceiptOutcome>();

  for (const agent of agents) if (agent.trim() !== "") ensureCommunicationNode(nodes, agent.trim());
  for (const claim of claims) if (claim.claim.owner !== "") ensureCommunicationNode(nodes, claim.claim.owner);

  for (const event of scoped) {
    if (isChatEvent(event)) {
      addChatEvent(event, nodes, edges, messageEdges);
      continue;
    }
    collectReceiptOutcomes(event, outcomes);
  }
  applyReceiptOutcomes(outcomes, messageEdges, edges, nodes);

  const resultEdges: CommunicationEdge[] = [...edges.values()]
    .map((edge) => ({ ...edge, health: edgeHealth(edge) }))
    .sort((left, right) => right.messages - left.messages || right.lastTs - left.lastTs || left.id.localeCompare(right.id));
  return {
    nodes: [...nodes.values()].sort(
      (left, right) => right.messages - left.messages || right.lastTs - left.lastTs || left.id.localeCompare(right.id),
    ),
    edges: resultEdges,
    projects: projectTraffic(nodes, claims),
    messages: resultEdges.reduce((total, edge) => total + edge.messages, 0),
  };
}
