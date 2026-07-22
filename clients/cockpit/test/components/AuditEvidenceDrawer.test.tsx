// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — exact audit evidence drawer tests

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuditEvidenceDrawer } from "../../src/components/AuditEvidenceDrawer";
import type { AuditEvidence } from "../../src/lib/auditEvidence";

afterEach(cleanup);

function evidence(kind: AuditEvidence["kind"]): AuditEvidence {
  return {
    seq: 7,
    kind,
    receipts: kind === "action-only" ? [] : [{
      seq: 7,
      ts: 7,
      receiptId: "operator-relay:7",
      kind: "operator-relay",
      subject: "T",
      actor: "ops",
      status: "applied",
      summary: "release T applied",
      sourceEventKind: "operator_relay",
    }],
    actions: kind === "receipt-only" ? [] : [{
      seq: 7,
      ts: 7,
      action: "release",
      direction: "in",
      status: "applied",
      applied: true,
      pending: false,
      namespace: "TEAM",
      taskId: "T",
      operator: "ops",
      agent: "",
      requester: "",
      approver: "",
      reason: "",
      detail: "released",
    }],
  };
}

describe("AuditEvidenceDrawer", () => {
  it("focuses close, states the exact association, and opens the retained event", async () => {
    const onOpenEvent = vi.fn();
    render(<AuditEvidenceDrawer evidence={evidence("paired-projection")} onClose={() => {}} onOpenEvent={onOpenEvent} />);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close audit evidence" }));
    expect(screen.getByText(/Exact association/u)).toBeTruthy();
    expect(screen.getByText("operator-relay:7")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "open exact event #7" }));
    expect(onOpenEvent).toHaveBeenCalledWith(7);
  });

  it("states partial action-only evidence and closes with Escape", () => {
    const onClose = vi.fn();
    render(<AuditEvidenceDrawer evidence={evidence("action-only")} onClose={onClose} onOpenEvent={() => {}} />);
    expect(screen.getByText(/Partial evidence/u)).toBeTruthy();
    expect(screen.getByText("No receipt projection retained at this sequence.")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("states receipt-only evidence and closes from the backdrop", () => {
    const onClose = vi.fn();
    const { container } = render(<AuditEvidenceDrawer evidence={evidence("receipt-only")} onClose={onClose} onOpenEvent={() => {}} />);
    expect(screen.getByText(/Receipt evidence only/u)).toBeTruthy();
    expect(screen.getByText("No governed action projection retained at this sequence.")).toBeTruthy();
    const veil = container.querySelector(".audit-drawer__veil");
    fireEvent.mouseDown(veil as Element);
    expect(onClose).toHaveBeenCalled();
  });
});
