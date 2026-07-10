// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — deterministic topology layout tests

import { describe, expect, it } from "vitest";
import type { BranchConflictView, ClaimView } from "../src/lib/claims";
import type { PeeringView } from "../src/lib/federation";
import { layoutFederation, layoutTopology, ROW_PITCH } from "../src/lib/topology";
import type { ClaimRecord } from "../src/types";

function claim(owner: string, taskId: string, overrides: Partial<ClaimRecord> = {}): ClaimView {
  const record: ClaimRecord = {
    task_id: taskId,
    owner,
    lease_expires_at: null,
    paths: [],
    stale: false,
    git: null,
    ...overrides,
  };
  return {
    claim: record,
    urgency: record.stale ? "stale" : "held",
    inConflict: false,
    secondsToExpiry: null,
  };
}

function conflict(a: string, b: string): BranchConflictView {
  return {
    ownerA: a,
    branchA: "x",
    baseA: "main",
    ownerB: b,
    branchB: "y",
    baseB: "main",
    paths: [],
    description: `${a} vs ${b}`,
  };
}

describe("layoutTopology", () => {
  it("lays out agents and tasks alphabetically with one edge per claim", () => {
    const layout = layoutTopology(
      [claim("beta/agent", "t2"), claim("alfa/agent", "t1")],
      [],
      5,
    );
    expect(layout.agents.map((node) => node.name)).toEqual(["alfa/agent", "beta/agent"]);
    expect(layout.tasks.map((node) => node.taskId)).toEqual(["t1", "t2"]);
    expect(layout.agents[0]?.y).toBe(ROW_PITCH);
    expect(layout.claims).toHaveLength(2);
    expect(layout.claims[0]).toMatchObject({ agent: "beta/agent", taskId: "t2", state: "active" });
    expect(layout.idleAgents).toBe(3);
    expect(layout.height).toBe(3 * ROW_PITCH + ROW_PITCH / 2);
  });

  it("marks stale claims, conflict-involved edges, and conflict agents", () => {
    const stale = claim("a", "old", { stale: true });
    const contested: ClaimView = { ...claim("fighter", "hot"), inConflict: true };
    const layout = layoutTopology([stale, contested], [conflict("fighter", "rival")], 2);
    expect(layout.tasks.find((node) => node.taskId === "old")?.stale).toBe(true);
    expect(layout.claims.find((edge) => edge.taskId === "old")?.state).toBe("stale");
    expect(layout.claims.find((edge) => edge.taskId === "hot")?.state).toBe("conflict");
    expect(layout.agents.find((node) => node.name === "fighter")?.inConflict).toBe(true);
    // `rival` holds nothing but sits in the conflict, so it IS drawn.
    expect(layout.agents.map((node) => node.name)).toContain("rival");
    expect(layout.conflicts).toHaveLength(1);
    expect(layout.idleAgents).toBe(0);
  });

  it("dedupes conflict ties per pair and skips ties to unknown agents", () => {
    const layout = layoutTopology(
      [claim("a", "t")],
      [conflict("a", "b"), conflict("b", "a"), { ...conflict("ghost", ""), ownerB: "" }],
      3,
    );
    expect(layout.conflicts).toHaveLength(1);
    // "ghost" enters the agent column via the conflict record's owner A.
    expect(layout.agents.map((node) => node.name)).toEqual(["a", "b", "ghost"]);
  });

  it("skips an empty owner on either side of a conflict", () => {
    // ownerA empty exercises the other guard: only the named side is drawn.
    const layout = layoutTopology([], [{ ...conflict("", "named"), ownerA: "" }], 1);
    expect(layout.agents.map((node) => node.name)).toEqual(["named"]);
  });

  it("lays out the federation band: sorted peers, centred hub, joined detail", () => {
    const peering = (domain: string, overrides: Partial<PeeringView> = {}): PeeringView => ({
      domain,
      state: "active",
      importedAt: null,
      confirmedBy: "",
      source: "",
      fingerprint: "",
      expiresAt: null,
      ...overrides,
    });
    const band = layoutFederation([
      peering("ml350", { confirmedBy: "operator", fingerprint: "ab:cd" }),
      peering("laptop", { state: "revoked", source: "offer:laptop" }),
    ]);
    expect(band.peers.map((peer) => peer.domain)).toEqual(["laptop", "ml350"]);
    expect(band.peers[0]?.y).toBe(ROW_PITCH);
    expect(band.peers[0]?.detail).toBe("revoked · source offer:laptop");
    expect(band.peers[1]?.detail).toBe("active · confirmed by operator · fingerprint ab:cd");
    expect(band.hubY).toBeGreaterThan(ROW_PITCH / 2);
    expect(band.height).toBe(3 * ROW_PITCH + ROW_PITCH / 2);
    expect(layoutFederation([])).toMatchObject({ peers: [], hubY: ROW_PITCH });
  });

  it("skips claims whose owner or task is blank and handles the empty fleet", () => {
    const layout = layoutTopology([claim("", "t"), claim("a", "")], [], 1);
    expect(layout.claims).toEqual([]);
    expect(layoutTopology([], [], 0)).toMatchObject({
      agents: [],
      tasks: [],
      claims: [],
      conflicts: [],
      idleAgents: 0,
    });
  });
});
