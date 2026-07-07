// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — reliability evidence panel behaviour tests

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ReliabilityPanel } from "../../src/components/ReliabilityPanel";
import type { ReliabilityFinding, ReliabilityReport, ReliabilityState } from "../../src/lib/reliability";

afterEach(cleanup);

function finding(kind: string, seq: number, detail: string): ReliabilityFinding {
  return { kind, owner: "quantum/claude", taskId: "t-1", seq, ts: 1_751_800_000, detail, evidence: { seq } };
}

function reportOf(findings: readonly ReliabilityFinding[], note = "audit signals, not scores"): ReliabilityReport {
  return {
    asOf: 1_751_800_000,
    generatedFromSeq: 99,
    note,
    owners: [
      { owner: "quantum/claude", staleClaims: 2, conflictPairs: 1, declaredFailedChecks: 0, brokenHandoffs: 3 },
    ],
    findings,
  };
}

function stateOf(data: ReliabilityReport | null, status: ReliabilityState["status"], error: string | null = null): ReliabilityState {
  return { data, status, fetchedAt: data === null ? null : 1, error };
}

describe("ReliabilityPanel", () => {
  it("states the three no-data conditions as distinct facts", () => {
    render(<ReliabilityPanel state={stateOf(null, "absent")} />);
    expect(screen.getByText(/does not serve reliability evidence yet/)).toBeTruthy();
    cleanup();
    render(<ReliabilityPanel state={stateOf(null, "error", "hub returned 500")} />);
    expect(screen.getByText("Reliability feed failed: hub returned 500")).toBeTruthy();
    cleanup();
    render(<ReliabilityPanel state={stateOf(null, "connecting")} />);
    expect(screen.getByText("Waiting for the hub.")).toBeTruthy();
  });

  it("shows the owner evidence table and the hub's own boundary note", () => {
    render(<ReliabilityPanel state={stateOf(reportOf([]), "live")} />);
    expect(screen.getByText("audit signals, not scores")).toBeTruthy();
    const row = screen.getByText("quantum/claude").closest("tr");
    expect(row?.textContent).toContain("2");
    expect(row?.textContent).toContain("3");
    expect(screen.getByText("No reliability findings recorded.")).toBeTruthy();
  });

  it("glyphs each finding kind and collapses the tail beyond forty", () => {
    const findings = [
      finding("stale_claim", 1, "lease lapsed"),
      finding("conflict_pair", 2, "both branches touch a path"),
      finding("declared_failed_check", 3, "check failed"),
      finding("broken_handoff_candidate", 4, "handoff never picked up"),
      finding("novel_kind", 5, "unknown class"),
      ...Array.from({ length: 40 }, (_, index) => finding("stale_claim", 10 + index, `filler ${index}`)),
    ];
    render(<ReliabilityPanel state={stateOf(reportOf(findings), "live")} />);
    expect(screen.getByText("lease lapsed")).toBeTruthy();
    expect(screen.getByText("both branches touch a path").closest("li")?.className).toContain(
      "evidence-row--critical",
    );
    expect(screen.getByText("lease lapsed").closest("li")?.className).toContain("evidence-row--warn");
    expect(screen.getByText("seq 5")).toBeTruthy();
    expect(screen.getByText("+5 more recorded")).toBeTruthy();
  });
});
