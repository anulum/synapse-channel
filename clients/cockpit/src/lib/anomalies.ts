// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — client-side anomaly heuristics over the observed event window

// Temporal auto-flags a workflow after consecutive task failures; the bus
// analogue is repetition the operator should look at: a task claimed over and
// over, or a lease that keeps expiring. These are HEURISTICS computed from the
// observed window only — they are labelled as such wherever rendered, they are
// never scores, and they disappear when the pattern stops appearing in the
// window. The hub's own risk view remains the authoritative triage.

import type { CockpitEvent } from "../types";

/** How many claim events on one task the observed window tolerates unflagged. */
export const CLAIM_CHURN_THRESHOLD = 3;

/** How many lease expiries on one task the observed window tolerates unflagged. */
export const LEASE_REPEAT_THRESHOLD = 2;

/** One heuristic flag over the observed window; a pointer, not a verdict. */
export interface AnomalyFlag {
  readonly taskId: string;
  readonly kind: "claim_churn" | "lease_repeat";
  /** How many times the pattern appeared in the observed window. */
  readonly count: number;
  /** Timestamp of the newest contributing event. */
  readonly lastTs: number;
  /** Plain statement of the observation, counts included. */
  readonly detail: string;
}

/**
 * Scan the observed window for repetition heuristics: a task with
 * `CLAIM_CHURN_THRESHOLD`+ claim events (churn — claimed again and again) and
 * a task with `LEASE_REPEAT_THRESHOLD`+ lease expiries. Flags order by newest
 * contributing event. Counts state exactly what was observed; whether it is a
 * problem is the operator's call.
 */
export function deriveAnomalies(events: readonly CockpitEvent[]): AnomalyFlag[] {
  const claims = new Map<string, { count: number; lastTs: number }>();
  const leases = new Map<string, { count: number; lastTs: number }>();
  for (const event of events) {
    if (event.taskId === "") continue;
    const bucket = event.kind === "claim" ? claims : event.kind === "lease" ? leases : null;
    if (bucket === null) continue;
    const entry = bucket.get(event.taskId);
    if (entry === undefined) bucket.set(event.taskId, { count: 1, lastTs: event.ts });
    else {
      entry.count += 1;
      if (event.ts > entry.lastTs) entry.lastTs = event.ts;
    }
  }

  const flags: AnomalyFlag[] = [];
  for (const [taskId, entry] of claims) {
    if (entry.count >= CLAIM_CHURN_THRESHOLD) {
      flags.push({
        taskId,
        kind: "claim_churn",
        count: entry.count,
        lastTs: entry.lastTs,
        detail: `claimed ${entry.count}x in the observed window`,
      });
    }
  }
  for (const [taskId, entry] of leases) {
    if (entry.count >= LEASE_REPEAT_THRESHOLD) {
      flags.push({
        taskId,
        kind: "lease_repeat",
        count: entry.count,
        lastTs: entry.lastTs,
        detail: `lease expired ${entry.count}x in the observed window`,
      });
    }
  }
  flags.sort((a, b) => b.lastTs - a.lastTs || a.taskId.localeCompare(b.taskId));
  return flags;
}
