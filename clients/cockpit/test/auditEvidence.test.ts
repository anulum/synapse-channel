// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — exact audit evidence association tests

import { describe, expect, it } from "vitest";
import { auditEvidenceAt, auditEvidenceRows } from "../src/lib/auditEvidence";
import type { OperatorActionRow, ReceiptRow } from "../src/lib/auditFeeds";

function receipt(seq: number, kind = "operator-relay"): ReceiptRow {
  return {
    seq,
    ts: seq,
    receiptId: `receipt:${seq}:${kind}`,
    kind,
    subject: "T",
    actor: "operator",
    status: "applied",
    summary: "recorded",
    sourceEventKind: "operator_relay",
  };
}

function action(seq: number): OperatorActionRow {
  return {
    seq,
    ts: seq,
    action: "release",
    direction: "in",
    status: "applied",
    applied: true,
    pending: false,
    namespace: "TEAM",
    taskId: "T",
    operator: "operator",
    agent: "",
    requester: "",
    approver: "",
    reason: "",
    detail: "released",
  };
}

describe("auditEvidenceAt", () => {
  it("pairs only an operator-relay receipt and action at the exact same sequence", () => {
    expect(auditEvidenceAt([receipt(7)], [action(7), action(8)], 7)).toEqual({
      seq: 7,
      receipts: [receipt(7)],
      actions: [action(7)],
      kind: "paired-projection",
    });
    expect(auditEvidenceAt([receipt(7, "claim")], [action(7)], 7)?.kind).toBe("action-only");
  });

  it("keeps receipt-only, action-only, and absent evidence distinct", () => {
    expect(auditEvidenceAt([receipt(4)], [], 4)?.kind).toBe("receipt-only");
    expect(auditEvidenceAt([], [action(5)], 5)?.kind).toBe("action-only");
    expect(auditEvidenceAt([receipt(4)], [action(5)], 6)).toBeNull();
  });
});

describe("auditEvidenceRows", () => {
  it("unions retained sequences newest first without similarity joins", () => {
    const rows = auditEvidenceRows([receipt(1), receipt(3)], [action(2), action(3)]);
    expect(rows.map((row) => [row.seq, row.kind])).toEqual([
      [3, "paired-projection"],
      [2, "action-only"],
      [1, "receipt-only"],
    ]);
  });
});
