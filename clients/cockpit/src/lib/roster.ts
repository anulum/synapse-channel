// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — derive the operator fleet roster from a fleet snapshot

import type { ClaimRecord, FleetSnapshot } from "../types";

/**
 * A row's worst condition. Ordered worst-first so it doubles as sort priority:
 * an operator's eye should land on `conflict` and `stale` before `holding`.
 */
export type RowStatus = "conflict" | "stale" | "holding" | "idle";

const STATUS_RANK: Record<RowStatus, number> = {
  conflict: 0,
  stale: 1,
  holding: 2,
  idle: 3,
};

/** One agent (or lingering claim owner) as a roster row. */
export interface RosterEntry {
  readonly agent: string;
  readonly status: RowStatus;
  /** Whether the identity is in the live roster right now. */
  readonly online: boolean;
  readonly activeClaims: readonly ClaimRecord[];
  readonly staleClaims: readonly ClaimRecord[];
  /** Every distinct path the identity holds, active or stale, sorted. */
  readonly paths: readonly string[];
  /** Whether the identity appears in an advisory branch conflict. */
  readonly inConflict: boolean;
  /**
   * Whether the hub expected this agent's `-rx` waiter and found it absent —
   * the agent is reachable-in-name only and will not wake on messages.
   */
  readonly wakerMissing: boolean;
}

function conflictOwners(snapshot: FleetSnapshot): ReadonlySet<string> {
  const owners = new Set<string>();
  for (const conflict of snapshot.fleet.branch_conflicts) {
    const a = conflict["owner_a"];
    const b = conflict["owner_b"];
    if (typeof a === "string") owners.add(a);
    if (typeof b === "string") owners.add(b);
  }
  return owners;
}

function statusFor(inConflict: boolean, hasStale: boolean, hasActive: boolean): RowStatus {
  if (inConflict) return "conflict";
  if (hasStale) return "stale";
  if (hasActive) return "holding";
  return "idle";
}

function uniqueSorted(paths: readonly string[]): string[] {
  return [...new Set(paths)].sort((a, b) => a.localeCompare(b));
}

function groupByOwner(claims: readonly ClaimRecord[]): Map<string, ClaimRecord[]> {
  const grouped = new Map<string, ClaimRecord[]>();
  for (const claim of claims) {
    const bucket = grouped.get(claim.owner);
    if (bucket === undefined) grouped.set(claim.owner, [claim]);
    else bucket.push(claim);
  }
  return grouped;
}

/**
 * Build the fleet roster from a snapshot. Rows cover every live agent plus any
 * owner still holding a claim (so a lingering stale claim from a departed agent
 * stays visible — that is the point of the risk lane). Rows are ordered
 * worst-status first, then by name, so trouble sits at the top.
 */
export function deriveRoster(snapshot: FleetSnapshot | null): RosterEntry[] {
  if (snapshot === null) return [];

  const live = new Set(snapshot.fleet.agents.live);
  const online = new Set([...snapshot.online_agents, ...live]);
  const inConflict = conflictOwners(snapshot);
  const missingWaiters = new Set(snapshot.fleet.agents.missing_waiters);

  const active = groupByOwner(snapshot.fleet.claims.active_claims);
  const stale = groupByOwner(snapshot.fleet.claims.stale_claims);

  const identities = new Set<string>([...live, ...active.keys(), ...stale.keys()]);

  const rows: RosterEntry[] = [];
  for (const agent of identities) {
    if (agent === "") continue;
    const activeClaims = active.get(agent) ?? [];
    const staleClaims = stale.get(agent) ?? [];
    const paths = uniqueSorted([
      ...activeClaims.flatMap((claim) => claim.paths),
      ...staleClaims.flatMap((claim) => claim.paths),
    ]);
    rows.push({
      agent,
      status: statusFor(inConflict.has(agent), staleClaims.length > 0, activeClaims.length > 0),
      online: online.has(agent),
      activeClaims,
      staleClaims,
      paths,
      inConflict: inConflict.has(agent),
      wakerMissing: missingWaiters.has(`${agent}-rx`),
    });
  }

  rows.sort((a, b) => STATUS_RANK[a.status] - STATUS_RANK[b.status] || a.agent.localeCompare(b.agent));
  return rows;
}
