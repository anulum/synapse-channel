// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — findings stream behaviour tests

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { FindingsStream } from "../../src/components/FindingsStream";
import type { FindingRow, FindingsState } from "../../src/lib/findings";
import { identText } from "../identText";

afterEach(cleanup);

const BLANK = {
  subkind: "",
  actor: "",
  project: "",
  evidenceKind: "",
  claimStatus: "",
  freshness: "",
  lifecycle: "",
  evidenceRef: "",
} as const;

const ROWS: readonly FindingRow[] = [
  {
    seq: 9,
    ts: 1_751_800_000,
    statement: "root cause found",
    subkind: "defect",
    actor: "quantum/claude",
    project: "QUANTUM",
    evidenceKind: "measured",
    claimStatus: "reference-validated",
    freshness: "verified-at-source",
    lifecycle: "active",
    evidenceRef: "experiments/k_nm.py:88",
  },
  { ...BLANK, seq: 4, ts: null, statement: "unattributed note" },
];

function live(findings: readonly FindingRow[], total = findings.length): FindingsState {
  return { data: { findings, total }, status: "live", fetchedAt: 1, error: null };
}

describe("FindingsStream", () => {
  it("says what it is waiting for, what is not served, and what failed", () => {
    render(<FindingsStream state={{ data: null, status: "connecting", fetchedAt: null, error: null }} />);
    expect(screen.getByText("Waiting for the hub.")).toBeTruthy();
    cleanup();
    render(<FindingsStream state={{ data: null, status: "absent", fetchedAt: null, error: null }} />);
    expect(
      screen.getByText("Findings are not served: start the dashboard with --feeds-db."),
    ).toBeTruthy();
    cleanup();
    render(<FindingsStream state={{ data: null, status: "error", fetchedAt: null, error: "hub returned 503" }} />);
    expect(screen.getByText("Findings could not be read: hub returned 503")).toBeTruthy();
    cleanup();
    render(<FindingsStream state={{ data: null, status: "error", fetchedAt: null, error: null }} />);
    expect(screen.getByText("Findings could not be read: unknown error")).toBeTruthy();
    cleanup();
    render(<FindingsStream state={live([])} />);
    expect(screen.getByText("No findings recorded.")).toBeTruthy();
  });

  it("shows time, author, project, kind, claim, freshness and statement, dashes for the unrecorded", () => {
    render(<FindingsStream state={live(ROWS)} />);
    const statement = screen.getByText("root cause found");
    expect(statement.className).toBe("finding-row__text");
    expect(statement.getAttribute("title")).toBe("Evidence: experiments/k_nm.py:88");
    expect(screen.getByText(identText("quantum/claude")).className).toContain("finding-row__author");
    expect(screen.getByText(identText("QUANTUM"))).toBeTruthy();
    expect(screen.getByText("defect").className).toBe("finding-row__axis");
    expect(screen.getByText("reference-validated").getAttribute("title")).toBe("Claim status");
    const fresh = screen.getByText("verified-at-source");
    expect(fresh.className).toBe("finding-row__axis finding-row__axis--verified");
    expect(fresh.getAttribute("title")).toBe(
      "The producer re-checked the reference at its source in this session.",
    );
    expect(screen.queryByText("active")).toBeNull();
    const bare = screen.getByText("unattributed note");
    expect(bare.getAttribute("title")).toBeNull();
    expect(screen.getAllByText("—")).toHaveLength(2);
    expect(screen.getByText("2")).toBeTruthy();
  });

  it("marks an unchecked reference and a finding that no longer stands", () => {
    render(
      <FindingsStream
        state={live([
          { ...BLANK, seq: 3, ts: 1, statement: "old claim", freshness: "traceable-unchecked", lifecycle: "retracted" },
          { ...BLANK, seq: 2, ts: 1, statement: "replaced claim", lifecycle: "superseded" },
        ])}
      />,
    );
    const unchecked = screen.getByText("traceable-unchecked");
    expect(unchecked.className).toBe("finding-row__axis");
    expect(unchecked.getAttribute("title")).toBe("Not re-checked at its source in this session.");
    expect(screen.getByText("retracted").className).toBe("finding-row__axis finding-row__axis--closed");
    expect(screen.getByText("superseded").className).toBe("finding-row__axis finding-row__axis--closed");
    expect(screen.getByText("old claim").className).toBe("finding-row__text finding-row__text--closed");
    expect(screen.getByText("replaced claim").className).toBe("finding-row__text finding-row__text--closed");
  });

  it("states a page of a longer log as N of M", () => {
    render(<FindingsStream state={live(ROWS, 75)} />);
    expect(screen.getByText("2 of 75").getAttribute("title")).toBe(
      "The newest findings; older ones stay in the log.",
    );
  });
});
