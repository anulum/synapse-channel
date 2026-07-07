// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — topology view behaviour tests

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { TopologyView } from "../../src/components/TopologyView";
import type { BranchConflictView, ClaimView } from "../../src/lib/claims";
import { parseFederation, type FederationState } from "../../src/lib/federation";

afterEach(cleanup);

function view(taskId: string, owner: string, options: { stale?: boolean; inConflict?: boolean } = {}): ClaimView {
  return {
    claim: { task_id: taskId, owner, lease_expires_at: null, paths: ["p"], stale: options.stale ?? false, git: null },
    urgency: options.stale === true ? "stale" : "held",
    inConflict: options.inConflict ?? false,
    secondsToExpiry: null,
  };
}

const CONFLICT: BranchConflictView = {
  ownerA: "quantum/claude",
  branchA: "feat-a",
  baseA: "main",
  ownerB: "quantum/codex",
  branchB: "feat-b",
  baseB: "main",
  paths: ["src/shared.py"],
  description: "overlap",
};

function federationOf(raw: unknown, status: FederationState["status"], error: string | null = null): FederationState {
  return { data: parseFederation(raw), status, fetchedAt: 1, error };
}

describe("TopologyView", () => {
  it("waits, then states an empty topology honestly", () => {
    render(<TopologyView claims={[]} conflicts={[]} liveAgentCount={0} connected={false} />);
    expect(screen.getByText("Waiting for the hub.")).toBeTruthy();
    cleanup();
    render(<TopologyView claims={[]} conflicts={[]} liveAgentCount={3} connected />);
    expect(
      screen.getByText("No file scopes are held right now — there is no topology to draw."),
    ).toBeTruthy();
  });

  it("draws claim edges, conflict ties, stale task nodes, and states idle agents not drawn", () => {
    render(
      <TopologyView
        connected
        liveAgentCount={5}
        claims={[
          view("t-1", "quantum/claude", { inConflict: true }),
          view("t-2", "quantum/codex", { inConflict: true }),
          view("t-3", "fusion/gemini", { stale: true }),
        ]}
        conflicts={[CONFLICT]}
      />,
    );
    expect(screen.getByText(/idle agents not drawn/)).toBeTruthy();
    expect(document.querySelectorAll(".topology__edge")).toHaveLength(3);
    expect(document.querySelectorAll(".topology__tie")).toHaveLength(1);
    expect(document.querySelectorAll(".topology__node--conflict")).toHaveLength(2);
    expect(document.querySelectorAll(".topology__node--stale")).toHaveLength(1);
    // The SVG label carries the id twice (a <title> for hover plus the text node).
    expect(screen.getAllByText("t-3").length).toBeGreaterThan(0);
    expect(screen.getByText("claude")).toBeTruthy();
  });

  it("keeps the federation band honest across absent, error, and single-hub states", () => {
    render(<TopologyView claims={[]} conflicts={[]} liveAgentCount={0} connected />);
    expect(screen.getByText(/Posture surface not served/)).toBeTruthy();
    cleanup();
    render(
      <TopologyView
        claims={[]}
        conflicts={[]}
        liveAgentCount={0}
        connected
        federation={{ data: null, status: "error", fetchedAt: null, error: "boom" }}
      />,
    );
    expect(screen.getByText("Federation feed failed: boom")).toBeTruthy();
    cleanup();
    render(
      <TopologyView
        claims={[]}
        conflicts={[]}
        liveAgentCount={0}
        connected
        federation={federationOf({ hub_id: "h", domain: "d", peerings: [], namespaces: [] }, "live")}
      />,
    );
    expect(screen.getByText("No peerings imported — a single-hub posture.")).toBeTruthy();
  });

  it("draws the peering band with lifecycle-coloured edges", () => {
    render(
      <TopologyView
        claims={[]}
        conflicts={[]}
        liveAgentCount={0}
        connected
        federation={federationOf(
          {
            hub_id: "h",
            domain: "alpha",
            peerings: [
              { domain: "beta", state: "active" },
              { domain: "gamma", state: "expired" },
            ],
            namespaces: [],
          },
          "live",
        )}
      />,
    );
    expect(screen.getByText("this hub")).toBeTruthy();
    expect(document.querySelectorAll(".topology__peer-edge--active")).toHaveLength(1);
    expect(document.querySelectorAll(".topology__peer-edge--expired")).toHaveLength(1);
    expect(screen.getByText("beta")).toBeTruthy();
    expect(screen.getByText("gamma")).toBeTruthy();
  });
});
