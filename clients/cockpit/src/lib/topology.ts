// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the fleet topology layout: who holds what, drawn as a graph

// The field's topology panels run force-directed physics; this cockpit draws
// the same facts deterministically — agents in one column, tasks in the other,
// a line per claim, a red tie per advisory conflict. Deterministic beats
// pretty-but-jittery on an instrument: the same fleet state always renders the
// same picture, and there is no physics library in the dependency tree. Only
// agents that hold something (or sit in a conflict) are drawn; the idle rest
// is a stated count, not visual noise.

import type { BranchConflictView, ClaimView } from "./claims";
import type { PeeringView } from "./federation";

/** Vertical pitch between nodes, in SVG user units. */
export const ROW_PITCH = 26;

/** One drawn agent node. */
export interface AgentNode {
  readonly name: string;
  readonly y: number;
  /** Whether the agent sits on either side of an advisory branch conflict. */
  readonly inConflict: boolean;
}

/** One drawn task node. */
export interface TaskNode {
  readonly taskId: string;
  readonly y: number;
  /** Whether any claim on this task is stale. */
  readonly stale: boolean;
}

/** One claim edge from an agent to the task it holds. */
export interface ClaimEdge {
  readonly agent: string;
  readonly taskId: string;
  readonly fromY: number;
  readonly toY: number;
  readonly state: "active" | "stale" | "conflict";
}

/** One agent↔agent conflict tie (drawn inside the agent column). */
export interface ConflictTie {
  readonly a: string;
  readonly b: string;
  readonly fromY: number;
  readonly toY: number;
}

/** The deterministic bipartite layout the topology panel renders. */
export interface TopologyLayout {
  readonly agents: readonly AgentNode[];
  readonly tasks: readonly TaskNode[];
  readonly claims: readonly ClaimEdge[];
  readonly conflicts: readonly ConflictTie[];
  /** Live agents holding nothing — stated, not drawn. */
  readonly idleAgents: number;
  /** SVG height covering the taller column. */
  readonly height: number;
}

/**
 * Lay out the claim topology: agents that hold a claim (or sit in a conflict)
 * on the left, claimed tasks on the right, one edge per claim, one tie per
 * advisory conflict pair. Rows sort alphabetically, so the picture is stable
 * across refreshes; `liveAgentCount` minus the drawn agents is the stated
 * idle remainder.
 */
/** One drawn peer-domain node in the federation band. */
export interface PeerNode {
  readonly domain: string;
  readonly y: number;
  /** Peering lifecycle state the durable store proves. */
  readonly state: string;
  /** Tooltip material: provenance and fingerprint, joined and non-empty parts only. */
  readonly detail: string;
}

/** The federation band: this hub on the left, imported peerings on the right. */
export interface FederationBand {
  readonly peers: readonly PeerNode[];
  /** This hub's node y — vertically centred against the peer ladder. */
  readonly hubY: number;
  /** SVG height covering the peer ladder. */
  readonly height: number;
}

/**
 * Lay out the federation band from the imported peerings, sorted by domain.
 * An empty store yields an empty band (the panel states it in words instead).
 */
export function layoutFederation(peerings: readonly PeeringView[]): FederationBand {
  const ordered = [...peerings].sort((a, b) => a.domain.localeCompare(b.domain));
  const peers: PeerNode[] = ordered.map((peering, index) => ({
    domain: peering.domain,
    y: (index + 1) * ROW_PITCH,
    state: peering.state,
    detail: [
      peering.state,
      peering.confirmedBy === "" ? "" : `confirmed by ${peering.confirmedBy}`,
      peering.source === "" ? "" : `source ${peering.source}`,
      peering.fingerprint === "" ? "" : `fingerprint ${peering.fingerprint}`,
    ]
      .filter((part) => part !== "")
      .join(" · "),
  }));
  const height = (peers.length + 1) * ROW_PITCH + ROW_PITCH / 2;
  return {
    peers,
    hubY: peers.length === 0 ? ROW_PITCH : (ROW_PITCH + peers.length * ROW_PITCH) / 2 + ROW_PITCH / 2,
    height,
  };
}

export function layoutTopology(
  claims: readonly ClaimView[],
  conflicts: readonly BranchConflictView[],
  liveAgentCount: number,
): TopologyLayout {
  const conflictAgents = new Set<string>();
  for (const conflict of conflicts) {
    if (conflict.ownerA !== "") conflictAgents.add(conflict.ownerA);
    if (conflict.ownerB !== "") conflictAgents.add(conflict.ownerB);
  }

  const agentNames = new Set<string>(conflictAgents);
  const taskIds = new Set<string>();
  for (const view of claims) {
    if (view.claim.owner !== "") agentNames.add(view.claim.owner);
    if (view.claim.task_id !== "") taskIds.add(view.claim.task_id);
  }

  const agentOrder = [...agentNames].sort((a, b) => a.localeCompare(b));
  const taskOrder = [...taskIds].sort((a, b) => a.localeCompare(b));
  const agentY = new Map(agentOrder.map((name, index) => [name, (index + 1) * ROW_PITCH]));
  const taskY = new Map(taskOrder.map((taskId, index) => [taskId, (index + 1) * ROW_PITCH]));

  const staleTasks = new Set(
    claims.filter((view) => view.claim.stale).map((view) => view.claim.task_id),
  );

  const agents: AgentNode[] = agentOrder.map((name) => ({
    name,
    y: agentY.get(name) as number,
    inConflict: conflictAgents.has(name),
  }));
  const tasks: TaskNode[] = taskOrder.map((taskId) => ({
    taskId,
    y: taskY.get(taskId) as number,
    stale: staleTasks.has(taskId),
  }));

  const claimEdges: ClaimEdge[] = [];
  for (const view of claims) {
    const fromY = agentY.get(view.claim.owner);
    const toY = taskY.get(view.claim.task_id);
    if (fromY === undefined || toY === undefined) continue;
    claimEdges.push({
      agent: view.claim.owner,
      taskId: view.claim.task_id,
      fromY,
      toY,
      state: view.inConflict ? "conflict" : view.claim.stale ? "stale" : "active",
    });
  }

  const ties: ConflictTie[] = [];
  const seenPairs = new Set<string>();
  for (const conflict of conflicts) {
    const fromY = agentY.get(conflict.ownerA);
    const toY = agentY.get(conflict.ownerB);
    if (fromY === undefined || toY === undefined) continue;
    const key = [conflict.ownerA, conflict.ownerB].sort().join("\u0000");
    if (seenPairs.has(key)) continue;
    seenPairs.add(key);
    ties.push({ a: conflict.ownerA, b: conflict.ownerB, fromY, toY });
  }

  return {
    agents,
    tasks,
    claims: claimEdges,
    conflicts: ties,
    idleAgents: Math.max(0, liveAgentCount - agents.length),
    height: (Math.max(agents.length, tasks.length) + 1) * ROW_PITCH + ROW_PITCH / 2,
  };
}
