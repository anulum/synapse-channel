// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — exact-event receipt and governed-action association

import type { OperatorActionRow, ReceiptRow } from "./auditFeeds";

export type AuditEvidenceKind = "paired-projection" | "receipt-only" | "action-only";

/** Every audit projection carried by one exact durable event sequence. */
export interface AuditEvidence {
  readonly seq: number;
  readonly receipts: readonly ReceiptRow[];
  readonly actions: readonly OperatorActionRow[];
  readonly kind: AuditEvidenceKind;
}

function evidenceKind(
  receipts: readonly ReceiptRow[],
  actions: readonly OperatorActionRow[],
): AuditEvidenceKind {
  if (actions.length > 0 && receipts.some((receipt) => receipt.kind === "operator-relay")) {
    return "paired-projection";
  }
  return actions.length > 0 ? "action-only" : "receipt-only";
}

/** Return an exact-sequence association; task, actor, and timestamp similarity are ignored. */
export function auditEvidenceAt(
  receipts: readonly ReceiptRow[],
  actions: readonly OperatorActionRow[],
  seq: number,
): AuditEvidence | null {
  const matchingReceipts = receipts.filter((row) => row.seq === seq);
  const matchingActions = actions.filter((row) => row.seq === seq);
  if (matchingReceipts.length === 0 && matchingActions.length === 0) return null;
  return {
    seq,
    receipts: matchingReceipts,
    actions: matchingActions,
    kind: evidenceKind(matchingReceipts, matchingActions),
  };
}

/** List the retained audit sequences, newest first, with no inferred joins. */
export function auditEvidenceRows(
  receipts: readonly ReceiptRow[],
  actions: readonly OperatorActionRow[],
): readonly AuditEvidence[] {
  const sequences = [...new Set([...receipts.map((row) => row.seq), ...actions.map((row) => row.seq)])]
    .sort((left, right) => right - left);
  return sequences.map((seq) => auditEvidenceAt(receipts, actions, seq) as AuditEvidence);
}
