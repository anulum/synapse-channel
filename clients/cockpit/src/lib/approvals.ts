// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — pending two-person relay approvals: the quorum's live state

// The hub enforces a two-operator quorum on relayed actions: the first
// submission is recorded pending and applies nothing until a second,
// different operator joins it. Between those two moments the quorum used to
// be invisible. The state snapshot now carries the ledger's pending records
// (`state.pending_relay_approvals`, oldest first); the cockpit's job is to
// show who is being waited for, so the second operator knows to act.

import type { FleetSnapshot } from "../types";

/** One pending relay awaiting a second operator, as the ledger records it. */
export interface PendingApprovalView {
  /** The relayable action id (e.g. "task_update"). */
  readonly action: string;
  /** The namespace the action acts in. */
  readonly namespace: string;
  /** The task the action targets. */
  readonly taskId: string;
  /** The operator who first requested it — the one a second must join. */
  readonly requester: string;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/**
 * Read the ledger's pending relays from a snapshot. Tolerant field-by-field;
 * a hub from before the surface (pre `pending_relay_approvals`) yields an
 * empty list, and the server's oldest-first order is preserved — insertion
 * order is the quorum's own record, never re-sorted here.
 */
export function parsePendingApprovals(snapshot: FleetSnapshot | null): PendingApprovalView[] {
  if (snapshot === null) return [];
  const raw = snapshot.state["pending_relay_approvals"];
  if (!Array.isArray(raw)) return [];
  const pending: PendingApprovalView[] = [];
  for (const entry of raw) {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) continue;
    const record = entry as Record<string, unknown>;
    pending.push({
      action: asString(record["action"]),
      namespace: asString(record["namespace"]),
      taskId: asString(record["task_id"]),
      requester: asString(record["requester"]),
    });
  }
  return pending;
}
