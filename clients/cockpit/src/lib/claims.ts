// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — shape file-scope claims and branch conflicts for the claims board

import type { ClaimRecord, FleetSnapshot } from "../types";

/** One advisory branch conflict as the hub's fleet view reports it. */
export interface BranchConflictView {
  readonly ownerA: string;
  readonly branchA: string;
  readonly baseA: string;
  readonly ownerB: string;
  readonly branchB: string;
  readonly baseB: string;
  /** The overlapping paths both branches touch. */
  readonly paths: readonly string[];
  /** The hub's own one-line description of the conflict. */
  readonly description: string;
}

/** How urgent a claim row is; doubles as its sort rank and row styling. */
export type ClaimUrgency = "conflict" | "stale" | "held";

/** One claim row for the claims board, ranked and annotated for triage. */
export interface ClaimView {
  readonly claim: ClaimRecord;
  readonly urgency: ClaimUrgency;
  /** Whether either side of an advisory branch conflict holds this claim. */
  readonly inConflict: boolean;
  /**
   * Seconds until the lease expires (negative once past due), or null for an
   * open-ended lease. Derived from the caller's clock, not re-fetched.
   */
  readonly secondsToExpiry: number | null;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/**
 * Read the hub's advisory branch conflicts out of a fleet snapshot. Records
 * are tolerated field-by-field: a partial record yields empty strings, never
 * a crash — the loud rendering must survive a payload from an older hub.
 */
export function parseConflicts(snapshot: FleetSnapshot): BranchConflictView[] {
  return snapshot.fleet.branch_conflicts.map((record) => ({
    ownerA: asString(record["owner_a"]),
    branchA: asString(record["branch_a"]),
    baseA: asString(record["base_a"]),
    ownerB: asString(record["owner_b"]),
    branchB: asString(record["branch_b"]),
    baseB: asString(record["base_b"]),
    paths: Array.isArray(record["paths"])
      ? record["paths"].filter((path): path is string => typeof path === "string")
      : [],
    description: asString(record["description"]),
  }));
}

const URGENCY_RANK: Record<ClaimUrgency, number> = {
  conflict: 0,
  stale: 1,
  held: 2,
};

function urgencyOf(claim: ClaimRecord, inConflict: boolean): ClaimUrgency {
  if (inConflict) return "conflict";
  if (claim.stale) return "stale";
  return "held";
}

function expiryOf(claim: ClaimRecord, nowMs: number): number | null {
  if (claim.lease_expires_at === null) return null;
  return claim.lease_expires_at - nowMs / 1000;
}

/** Sort key: soonest lease first; open-ended leases sink below timed ones. */
function expiryRank(view: ClaimView): number {
  return view.secondsToExpiry === null ? Number.POSITIVE_INFINITY : view.secondsToExpiry;
}

/**
 * Build the claims board's rows from a fleet snapshot: every active and stale
 * claim, annotated with conflict involvement and lease countdown, ordered
 * worst-first (conflict, then stale, then soonest expiry, then task id) so the
 * operator's eye lands on trouble.
 */
export function deriveClaims(snapshot: FleetSnapshot | null, nowMs: number): ClaimView[] {
  if (snapshot === null) return [];
  const conflicts = parseConflicts(snapshot);
  const conflicted = new Set<string>();
  for (const conflict of conflicts) {
    conflicted.add(conflict.ownerA);
    conflicted.add(conflict.ownerB);
  }
  const rows = [
    ...snapshot.fleet.claims.active_claims,
    ...snapshot.fleet.claims.stale_claims,
  ].map((claim): ClaimView => {
    const inConflict = conflicted.has(claim.owner);
    return {
      claim,
      urgency: urgencyOf(claim, inConflict),
      inConflict,
      secondsToExpiry: expiryOf(claim, nowMs),
    };
  });
  rows.sort(
    (a, b) =>
      URGENCY_RANK[a.urgency] - URGENCY_RANK[b.urgency] ||
      expiryRank(a) - expiryRank(b) ||
      a.claim.task_id.localeCompare(b.claim.task_id),
  );
  return rows;
}

/**
 * Render a lease countdown for display: `12:05` while running, `-0:42` once
 * past due, `no lease` for an open-ended hold. Minutes are not capped, so a
 * one-hour lease reads `60:00`.
 */
export function formatCountdown(secondsToExpiry: number | null): string {
  if (secondsToExpiry === null) return "no lease";
  const overdue = secondsToExpiry < 0;
  const total = Math.floor(Math.abs(secondsToExpiry));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  const stamp = `${minutes}:${String(seconds).padStart(2, "0")}`;
  return overdue ? `-${stamp}` : stamp;
}
