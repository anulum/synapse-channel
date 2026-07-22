// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — deterministic communication and project projections

import { eventsInWindow, type TimeWindow } from "./brush";
import type { ClaimView } from "./claims";
import type { CockpitEvent } from "../types";

export type DeliveryHealth = "healthy" | "deferred" | "failed" | "unknown";
export type SemanticResponseStatus = "acknowledged" | "in_progress" | "needs_input" | "declined" | "completed";
export type SemanticResponseEvidenceScope = "recipient" | "operator_commentary";

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

export interface ProjectTraffic {
  readonly id: string;
  readonly members: readonly string[];
  readonly inbound: number;
  readonly outbound: number;
  readonly claims: number;
  readonly lastTs: number;
}

export interface CommunicationModel {
  readonly nodes: readonly CommunicationNode[];
  readonly edges: readonly CommunicationEdge[];
  readonly projects: readonly ProjectTraffic[];
  readonly messages: number;
}

export interface ConversationMessage {
  readonly seq: number;
  readonly ts: number;
  readonly source: string;
  readonly target: string;
  readonly body: string;
  readonly delivery: ReceiptOutcome | "unknown";
  readonly responseToSeq: number | null;
  readonly responseStatus: SemanticResponseStatus | null;
  readonly responseEvidenceScope: SemanticResponseEvidenceScope | null;
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

type ReceiptOutcome = "delivered" | "deferred" | "failed";

const SEMANTIC_RESPONSE_STATUSES = new Set<SemanticResponseStatus>([
  "acknowledged",
  "in_progress",
  "needs_input",
  "declined",
  "completed",
]);
const SEMANTIC_RESPONSE_EVIDENCE_SCOPES = new Set<SemanticResponseEvidenceScope>([
  "recipient",
  "operator_commentary",
]);
const CONVERSATION_BODY_LIMIT = 500;

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function responseStatus(value: unknown): SemanticResponseStatus | null {
  return typeof value === "string" && SEMANTIC_RESPONSE_STATUSES.has(value as SemanticResponseStatus)
    ? (value as SemanticResponseStatus)
    : null;
}

function responseEvidenceScope(value: unknown): SemanticResponseEvidenceScope | null {
  return typeof value === "string" && SEMANTIC_RESPONSE_EVIDENCE_SCOPES.has(value as SemanticResponseEvidenceScope)
    ? (value as SemanticResponseEvidenceScope)
    : null;
}

function messageBody(value: unknown): string {
  if (typeof value !== "string") return "";
  return value.length > CONVERSATION_BODY_LIMIT ? `${value.slice(0, CONVERSATION_BODY_LIMIT)}…` : value;
}

export function projectOf(identity: string): string {
  const slash = identity.indexOf("/");
  if (slash > 0) return identity.slice(0, slash);
  if (identity === "all" || identity === "CEO") return "fleet-wide";
  return /^[A-Z][A-Z0-9-]+$/u.test(identity) ? identity : "unscoped";
}

function isExactIdentity(identity: string): boolean {
  return identity.includes("/") && !identity.includes("*") && !identity.includes("?");
}

function isChat(event: CockpitEvent): boolean {
  const payload = event.payload;
  if (payload === undefined) return false;
  return (
    text(payload["sender"]) !== "" &&
    text(payload["target"]) !== "" &&
    (payload["type"] === "chat" || Object.hasOwn(payload, "payload"))
  );
}

function receiptOutcome(event: CockpitEvent): ReceiptOutcome | null {
  const payload = event.payload;
  if (payload === undefined || !event.label.startsWith("delivery_receipt_")) return null;
  if (event.label === "delivery_receipt_expired" || payload["expired"] === true) return "failed";
  if (event.label === "delivery_receipt_deferred" || payload["deferred"] === true) return "deferred";
  if (event.label === "delivery_receipt_immediate") {
    return payload["delivered"] === true ? "delivered" : "failed";
  }
  return null;
}

function outcomeRank(outcome: ReceiptOutcome): number {
  return outcome === "failed" ? 3 : outcome === "deferred" ? 2 : 1;
}

function edgeHealth(edge: MutableEdge): DeliveryHealth {
  if (edge.failed > 0) return "failed";
  if (edge.deferred > 0) return "deferred";
  if (edge.delivered > 0) return "healthy";
  return "unknown";
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

  const ensureNode = (id: string): MutableNode => {
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
  };

  for (const agent of agents) if (agent.trim() !== "") ensureNode(agent.trim());
  for (const claim of claims) if (claim.claim.owner !== "") ensureNode(claim.claim.owner);

  for (const event of scoped) {
    if (isChat(event)) {
      const source = text(event.payload?.["sender"]);
      const target = text(event.payload?.["target"]);
      if (source === "" || target === "") continue;
      const sourceNode = ensureNode(source);
      const targetNode = ensureNode(target);
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
      continue;
    }
    const outcome = receiptOutcome(event);
    const messageSeq = finiteNumber(event.payload?.["message_seq"]);
    if (outcome === null || messageSeq === null) continue;
    const prior = outcomes.get(messageSeq);
    if (prior === undefined || outcomeRank(outcome) >= outcomeRank(prior)) {
      outcomes.set(messageSeq, outcome);
    }
  }

  for (const [messageSeq, outcome] of outcomes) {
    const edgeId = messageEdges.get(messageSeq);
    if (edgeId === undefined) continue;
    const edge = edges.get(edgeId);
    if (edge === undefined) continue;
    edge[outcome] += 1;
    const source = nodes.get(edge.source);
    const target = nodes.get(edge.target);
    if (source !== undefined) source[outcome] += 1;
    if (target !== undefined) target[outcome] += 1;
  }

  const claimCount = new Map<string, number>();
  for (const claim of claims) {
    const project = projectOf(claim.claim.owner);
    claimCount.set(project, (claimCount.get(project) ?? 0) + 1);
  }
  const projects = new Map<
    string,
    {
      members: Set<string>;
      inbound: number;
      outbound: number;
      lastTs: number;
    }
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

  const resultEdges: CommunicationEdge[] = [...edges.values()]
    .map((edge) => ({ ...edge, health: edgeHealth(edge) }))
    .sort((a, b) => b.messages - a.messages || b.lastTs - a.lastTs || a.id.localeCompare(b.id));
  return {
    nodes: [...nodes.values()].sort(
      (a, b) => b.messages - a.messages || b.lastTs - a.lastTs || a.id.localeCompare(b.id),
    ),
    edges: resultEdges,
    projects: [...projects.entries()]
      .map(([id, project]) => ({
        id,
        members: [...project.members].sort((a, b) => a.localeCompare(b)),
        inbound: project.inbound,
        outbound: project.outbound,
        claims: claimCount.get(id) ?? 0,
        lastTs: project.lastTs,
      }))
      .sort(
        (a, b) => b.inbound + b.outbound - (a.inbound + a.outbound) || b.lastTs - a.lastTs || a.id.localeCompare(b.id),
      ),
    messages: resultEdges.reduce((total, edge) => total + edge.messages, 0),
  };
}

/**
 * Build a bounded pairwise timeline only after an edge is selected. Unlike the
 * graph projection, this detail intentionally carries authenticated body text.
 */
export function deriveConversationDetail(
  events: readonly CockpitEvent[],
  source: string,
  target: string,
  window: TimeWindow | null = null,
  limit = 40,
): ConversationMessage[] {
  const scoped = eventsInWindow(events, window);
  const outcomes = new Map<number, ReceiptOutcome>();
  for (const event of scoped) {
    const outcome = receiptOutcome(event);
    const messageSeq = finiteNumber(event.payload?.["message_seq"]);
    if (outcome === null || messageSeq === null) continue;
    const prior = outcomes.get(messageSeq);
    if (prior === undefined || outcomeRank(outcome) >= outcomeRank(prior)) {
      outcomes.set(messageSeq, outcome);
    }
  }

  return scoped
    .filter((event) => {
      if (!isChat(event)) return false;
      const sender = text(event.payload?.["sender"]);
      const recipient = text(event.payload?.["target"]);
      return (sender === source && recipient === target) || (sender === target && recipient === source);
    })
    .map((event): ConversationMessage => {
      const responseTo = finiteNumber(event.payload?.["response_to_seq"]);
      return {
        seq: event.seq,
        ts: event.ts,
        source: text(event.payload?.["sender"]),
        target: text(event.payload?.["target"]),
        body: messageBody(event.payload?.["payload"]),
        delivery: outcomes.get(event.seq) ?? "unknown",
        responseToSeq: responseTo !== null && responseTo > 0 ? responseTo : null,
        responseStatus: responseStatus(event.payload?.["response_status"]),
        responseEvidenceScope: responseEvidenceScope(event.payload?.["response_evidence_scope"]),
      };
    })
    .sort((a, b) => b.ts - a.ts || b.seq - a.seq)
    .slice(0, Math.max(1, limit));
}

export interface WebNode extends CommunicationNode {
  readonly x: number;
  readonly y: number;
  readonly radius: number;
  readonly colourIndex: number;
}

export interface WebLayout {
  readonly nodes: readonly WebNode[];
  readonly byId: ReadonlyMap<string, WebNode>;
}

/** Stable circular sectors keep projects together and never jitter on refresh. */
export function layoutCommunicationWeb(model: CommunicationModel, width = 760, height = 360, limit = 28): WebLayout {
  // Quiet roster members belong in Projects, not as overlapping graph labels.
  // Bound the active web: the matrix is already bounded for the same reason.
  const visible = model.nodes.filter((node) => node.messages > 0).slice(0, Math.max(1, limit));
  const projectOrder = [...new Set(visible.map((node) => node.project))].sort((a, b) => a.localeCompare(b));
  const projectIndex = new Map(projectOrder.map((project, index) => [project, index]));
  const ordered = [...visible].sort(
    (a, b) => (projectIndex.get(a.project) ?? 0) - (projectIndex.get(b.project) ?? 0) || a.id.localeCompare(b.id),
  );
  const radius = Math.max(60, Math.min(width, height) * 0.38);
  const cx = width / 2;
  const cy = height / 2;
  const laid = ordered.map((node, index): WebNode => {
    const angle = -Math.PI / 2 + (index / Math.max(1, ordered.length)) * Math.PI * 2;
    return {
      ...node,
      x: cx + Math.cos(angle) * radius,
      y: cy + Math.sin(angle) * radius,
      radius: Math.min(10, 4 + Math.sqrt(node.messages) * 0.75),
      colourIndex: (projectIndex.get(node.project) ?? 0) % 6,
    };
  });
  return { nodes: laid, byId: new Map(laid.map((node) => [node.id, node])) };
}

/** The busiest exact identities, bounded so the matrix stays readable. */
export function matrixIdentities(model: CommunicationModel, limit = 12): CommunicationNode[] {
  return model.nodes.filter((node) => node.messages > 0).slice(0, Math.max(1, limit));
}
