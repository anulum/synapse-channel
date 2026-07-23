// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — strict durable audit document parsing contracts

import { describe, expect, it } from "vitest";

import {
  parseOperatorActionsPage,
  parseReceiptsPage,
} from "../src/lib/auditFeedParser";

function receipt(seq: number, summary = `receipt ${seq}`): Record<string, unknown> {
  return {
    seq,
    ts: seq * 10,
    receipt_id: `delivery:${seq}`,
    kind: "delivery",
    subject: `target-${seq}`,
    actor: "operator/test",
    status: "delivered",
    summary,
    source_event_kind: "delivery_receipt_immediate",
    payload: {},
  };
}

function action(seq: number): Record<string, unknown> {
  return {
    seq,
    ts: seq * 10,
    action: "release",
    direction: "out",
    status: "applied",
    applied: true,
    pending: false,
    namespace: "TEAM",
    task_id: `T-${seq}`,
    operator: "operator/test",
    agent: "",
    requester: "",
    approver: "",
    reason: "",
    detail: "released",
  };
}

describe("audit page parsers", () => {
  it("parses strict receipt and operator-action rows plus empty present pages", () => {
    expect(parseReceiptsPage({ present: true, receipts: [receipt(7)], next_cursor: 7 })).toEqual({
      rows: [
        {
          seq: 7,
          ts: 70,
          receiptId: "delivery:7",
          kind: "delivery",
          subject: "target-7",
          actor: "operator/test",
          status: "delivered",
          summary: "receipt 7",
          sourceEventKind: "delivery_receipt_immediate",
        },
      ],
      nextCursor: 7,
    });
    expect(
      parseOperatorActionsPage({ present: true, actions: [action(8)], next_cursor: 8 }),
    ).toEqual({
      rows: [
        {
          seq: 8,
          ts: 80,
          action: "release",
          direction: "out",
          status: "applied",
          applied: true,
          pending: false,
          namespace: "TEAM",
          taskId: "T-8",
          operator: "operator/test",
          agent: "",
          requester: "",
          approver: "",
          reason: "",
          detail: "released",
        },
      ],
      nextCursor: 8,
    });
    expect(parseReceiptsPage({ present: true, receipts: [], next_cursor: 0 })).toEqual({
      rows: [],
      nextCursor: 0,
    });
    expect(parseOperatorActionsPage({ present: true, actions: [], next_cursor: 0 })).toEqual({
      rows: [],
      nextCursor: 0,
    });
  });

  it("rejects malformed documents and every malformed receipt field", () => {
    expect(parseReceiptsPage(null)).toBeNull();
    expect(parseReceiptsPage([])).toBeNull();
    expect(parseReceiptsPage({ present: false, receipts: [], next_cursor: 0 })).toBeNull();
    expect(parseReceiptsPage({ present: true, receipts: "bad", next_cursor: 0 })).toBeNull();
    expect(parseReceiptsPage({ present: true, receipts: [], next_cursor: -1 })).toBeNull();
    expect(parseReceiptsPage({ present: true, receipts: ["bad"], next_cursor: 1 })).toBeNull();

    const textFields = [
      "receipt_id",
      "kind",
      "subject",
      "actor",
      "status",
      "summary",
      "source_event_kind",
    ];
    for (const field of textFields) {
      expect(
        parseReceiptsPage({
          present: true,
          receipts: [{ ...receipt(1), [field]: 7 }],
          next_cursor: 1,
        }),
        field,
      ).toBeNull();
    }
    for (const bad of ["1", -1, 1.5, Number.MAX_SAFE_INTEGER + 1]) {
      expect(
        parseReceiptsPage({
          present: true,
          receipts: [{ ...receipt(1), seq: bad }],
          next_cursor: 1,
        }),
      ).toBeNull();
    }
    for (const bad of ["1", -1, Number.POSITIVE_INFINITY]) {
      expect(
        parseReceiptsPage({
          present: true,
          receipts: [{ ...receipt(1), ts: bad }],
          next_cursor: 1,
        }),
      ).toBeNull();
    }
  });

  it("rejects every malformed operator-action field", () => {
    expect(
      parseOperatorActionsPage({ present: true, actions: ["bad"], next_cursor: 1 }),
    ).toBeNull();
    const textFields = [
      "action",
      "direction",
      "status",
      "namespace",
      "task_id",
      "operator",
      "agent",
      "requester",
      "approver",
      "reason",
      "detail",
    ];
    for (const field of textFields) {
      expect(
        parseOperatorActionsPage({
          present: true,
          actions: [{ ...action(1), [field]: null }],
          next_cursor: 1,
        }),
        field,
      ).toBeNull();
    }
    for (const field of ["applied", "pending"]) {
      expect(
        parseOperatorActionsPage({
          present: true,
          actions: [{ ...action(1), [field]: "yes" }],
          next_cursor: 1,
        }),
        field,
      ).toBeNull();
    }
    expect(
      parseOperatorActionsPage({
        present: true,
        actions: [{ ...action(1), seq: -1 }],
        next_cursor: 1,
      }),
    ).toBeNull();
    expect(
      parseOperatorActionsPage({
        present: true,
        actions: [{ ...action(1), ts: Number.NaN }],
        next_cursor: 1,
      }),
    ).toBeNull();
  });
});
