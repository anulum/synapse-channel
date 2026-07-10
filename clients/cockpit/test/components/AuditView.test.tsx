// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — receipt and operator audit rendering tests

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AuditView } from "../../src/components/AuditView";
import type {
  OperatorActionRow,
  OperatorActionsState,
  ReceiptRow,
  ReceiptsState,
} from "../../src/lib/auditFeeds";

afterEach(cleanup);

function receipt(seq: number, status = "delivered", summary = "delivery recorded"): ReceiptRow {
  return {
    seq,
    ts: 1_783_000_000 + seq,
    receiptId: `delivery:${seq}`,
    kind: "delivery",
    subject: `target-${seq}`,
    actor: "operator/test",
    status,
    summary,
    sourceEventKind: "delivery_receipt_immediate",
  };
}

function action(seq: number, overrides: Partial<OperatorActionRow> = {}): OperatorActionRow {
  return {
    seq,
    ts: 1_783_000_000 + seq,
    action: "release",
    direction: "out",
    status: "applied",
    applied: true,
    pending: false,
    namespace: "TEAM",
    taskId: `T-${seq}`,
    operator: "operator/test",
    agent: "",
    requester: "",
    approver: "",
    reason: "",
    detail: "released",
    ...overrides,
  };
}

function receipts(data: readonly ReceiptRow[] | null, status: ReceiptsState["status"]): ReceiptsState {
  return { data, status, fetchedAt: data === null ? null : 5_000, error: status === "error" ? "hub returned 503" : null };
}

function actions(
  data: readonly OperatorActionRow[] | null,
  status: OperatorActionsState["status"],
): OperatorActionsState {
  return { data, status, fetchedAt: data === null ? null : 5_000, error: status === "error" ? "hub returned 503" : null };
}

describe("AuditView", () => {
  it("distinguishes connecting, absent, and failed feeds without inventing emptiness", () => {
    const { rerender } = render(
      <AuditView receipts={receipts(null, "connecting")} operatorActions={actions(null, "absent")} />,
    );
    expect(screen.getByText("Waiting for the receipts feed.")).toBeTruthy();
    expect(screen.getByText(/Operator actions feed absent/u)).toBeTruthy();

    rerender(<AuditView receipts={receipts(null, "error")} operatorActions={actions(null, "error")} />);
    expect(screen.getByText(/Receipts feed failed: hub returned 503/u)).toBeTruthy();
    expect(screen.getByText(/Operator actions feed failed: hub returned 503/u)).toBeTruthy();
  });

  it("renders an empty present feed as a live, store-attested fact", () => {
    render(<AuditView receipts={receipts([], "live")} operatorActions={actions([], "live")} />);
    expect(screen.getByText("No universal receipts recorded.")).toBeTruthy();
    expect(screen.getByText("No governed operator relay actions recorded.")).toBeTruthy();
    expect(screen.getByText(/receipts live · actions live/u)).toBeTruthy();
  });

  it("distinguishes receipt kind/status/subject/actor and operator action/outcome", () => {
    const actionRows = [
      action(10),
      action(11, {
        taskId: "",
        operator: "",
        requester: "requester/test",
        detail: "",
        reason: "awaiting approval",
        applied: false,
        pending: true,
        status: "pending",
      }),
      action(12, {
        taskId: "",
        namespace: "",
        operator: "",
        requester: "",
        agent: "agent/test",
        detail: "",
        reason: "",
        applied: false,
        status: "refused",
      }),
      action(13, {
        operator: "",
        requester: "",
        agent: "",
        approver: "approver/test",
        detail: "",
        reason: "",
        direction: "",
        applied: false,
        status: "denied",
      }),
      action(14, {
        taskId: "",
        namespace: "",
        operator: "",
        requester: "",
        agent: "",
        approver: "",
        detail: "",
        reason: "",
        direction: "",
        applied: false,
        status: "refused",
      }),
    ];
    render(
      <AuditView
        receipts={receipts([receipt(1), receipt(2, "undelivered", "")], "live")}
        operatorActions={actions(actionRows, "live")}
      />,
    );
    expect(screen.getByText("delivery recorded")).toBeTruthy();
    expect(screen.getByText("receipt recorded")).toBeTruthy();
    expect(screen.getByText("target-1 · operator/test")).toBeTruthy();
    expect(screen.getByText("T-10 · operator/test")).toBeTruthy();
    expect(screen.getByText("TEAM · requester/test")).toBeTruthy();
    expect(screen.getByText("no subject · agent/test")).toBeTruthy();
    expect(screen.getByText("T-13 · approver/test")).toBeTruthy();
    expect(screen.getByText("no subject · no actor")).toBeTruthy();
    expect(screen.getByText("awaiting approval")).toBeTruthy();
    expect(screen.getByText("out")).toBeTruthy();
    expect(screen.getAllByText("operator relay recorded")).toHaveLength(2);
  });

  it("labels last-good rows stale after an error or endpoint disappearance", () => {
    render(
      <AuditView
        receipts={receipts([receipt(1)], "error")}
        operatorActions={actions([action(2)], "absent")}
      />,
    );
    expect(screen.getByText(/Receipts feed is stale \(hub returned 503\)/u)).toBeTruthy();
    expect(screen.getByText(/Operator actions feed is absent; showing the last/u)).toBeTruthy();
    expect(screen.getByText(/receipts stale · actions stale · endpoint absent/u)).toBeTruthy();
  });

  it("caps each rendered list while stating the retained remainder", () => {
    render(
      <AuditView
        receipts={receipts(Array.from({ length: 42 }, (_, index) => receipt(index + 1)), "live")}
        operatorActions={actions(Array.from({ length: 43 }, (_, index) => action(index + 100)), "live")}
      />,
    );
    expect(screen.getByText("+2 more retained receipts")).toBeTruthy();
    expect(screen.getByText("+3 more retained actions")).toBeTruthy();
  });
});
