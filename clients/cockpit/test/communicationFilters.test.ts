// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — communication-filter projection tests

import { describe, expect, it } from "vitest";

import {
  COMMUNICATION_QUERY_LIMIT,
  communicationFilterIsActive,
  communicationHealthFilter,
  filterCommunicationModel,
  normaliseCommunicationQuery,
} from "../src/lib/communicationFilters";
import type { CommunicationModel } from "../src/lib/communications";

const MODEL: CommunicationModel = {
  messages: 4,
  nodes: [
    { id: "alpha/one", project: "alpha", exact: true, messages: 2, inbound: 0, outbound: 2, delivered: 2, deferred: 0, failed: 0, lastTs: 3 },
    { id: "beta/two", project: "beta", exact: true, messages: 3, inbound: 2, outbound: 1, delivered: 2, deferred: 0, failed: 1, lastTs: 4 },
    { id: "gamma/three", project: "gamma", exact: true, messages: 3, inbound: 2, outbound: 1, delivered: 0, deferred: 1, failed: 1, lastTs: 5 },
    { id: "quiet/observer", project: "quiet", exact: true, messages: 0, inbound: 0, outbound: 0, delivered: 0, deferred: 0, failed: 0, lastTs: 0 },
  ],
  edges: [
    { id: "alpha/one\0beta/two", source: "alpha/one", target: "beta/two", messages: 2, delivered: 2, deferred: 0, failed: 0, lastTs: 3, health: "healthy" },
    { id: "beta/two\0gamma/three", source: "beta/two", target: "gamma/three", messages: 1, delivered: 0, deferred: 0, failed: 1, lastTs: 4, health: "failed" },
    { id: "gamma/three\0gamma/three", source: "gamma/three", target: "gamma/three", messages: 1, delivered: 0, deferred: 1, failed: 0, lastTs: 5, health: "deferred" },
  ],
  projects: [
    { id: "alpha", members: ["alpha/one"], inbound: 0, outbound: 2, claims: 2, lastTs: 3 },
    { id: "beta", members: ["beta/two"], inbound: 2, outbound: 1, claims: 1, lastTs: 4 },
    { id: "gamma", members: ["gamma/three"], inbound: 2, outbound: 1, claims: 0, lastTs: 5 },
    { id: "quiet", members: ["quiet/observer"], inbound: 0, outbound: 0, claims: 4, lastTs: 0 },
  ],
};

const SPARSE_MODEL: CommunicationModel = {
  messages: 3,
  nodes: [
    { id: "bare", project: "unscoped", exact: false, messages: 1, inbound: 0, outbound: 1, delivered: 0, deferred: 0, failed: 0, lastTs: 1 },
    { id: "same/a", project: "same", exact: true, messages: 1, inbound: 0, outbound: 1, delivered: 0, deferred: 0, failed: 0, lastTs: 1 },
    { id: "same/b", project: "same", exact: true, messages: 1, inbound: 1, outbound: 0, delivered: 0, deferred: 0, failed: 0, lastTs: 1 },
    { id: "orphan/z", project: "orphan", exact: true, messages: 1, inbound: 1, outbound: 0, delivered: 0, deferred: 0, failed: 0, lastTs: 1 },
  ],
  edges: [
    { id: "bare\0missing/target", source: "bare", target: "missing/target", messages: 1, delivered: 0, deferred: 0, failed: 0, lastTs: 1, health: "unknown" },
    { id: "same/a\0same/b", source: "same/a", target: "same/b", messages: 1, delivered: 0, deferred: 0, failed: 0, lastTs: 1, health: "unknown" },
    { id: "missing/source\0orphan/z", source: "missing/source", target: "orphan/z", messages: 1, delivered: 0, deferred: 0, failed: 0, lastTs: 1, health: "unknown" },
  ],
  projects: [
    { id: "unscoped", members: ["bare"], inbound: 0, outbound: 1, claims: 3, lastTs: 1 },
  ],
};

describe("communication filter primitives", () => {
  it("normalises bounded text and narrows untrusted health values", () => {
    expect(normaliseCommunicationQuery("  Alpha  ")).toBe("Alpha");
    expect(normaliseCommunicationQuery("x".repeat(COMMUNICATION_QUERY_LIMIT + 5))).toHaveLength(
      COMMUNICATION_QUERY_LIMIT,
    );
    expect(communicationHealthFilter("failed")).toBe("failed");
    expect(communicationHealthFilter("invented")).toBe("all");
  });

  it("recognises only filters that change the projection", () => {
    expect(communicationFilterIsActive({ query: "  ", health: "all" })).toBe(false);
    expect(communicationFilterIsActive({ query: "alpha", health: "all" })).toBe(true);
    expect(communicationFilterIsActive({ query: "", health: "unknown" })).toBe(true);
  });
});

describe("filterCommunicationModel", () => {
  it("returns the original projection for the default filter", () => {
    expect(filterCommunicationModel(MODEL, { query: "", health: "all" })).toBe(MODEL);
  });

  it("matches identities and projects case-insensitively, then recomputes totals", () => {
    const filtered = filterCommunicationModel(MODEL, { query: " ALPHA ", health: "all" });
    expect(filtered.messages).toBe(2);
    expect(filtered.edges.map((edge) => edge.id)).toEqual(["alpha/one\0beta/two"]);
    expect(filtered.nodes.find((node) => node.id === "alpha/one")).toMatchObject({
      messages: 2, inbound: 0, outbound: 2, delivered: 2,
    });
    expect(filtered.nodes.find((node) => node.id === "beta/two")).toMatchObject({
      messages: 2, inbound: 2, outbound: 0, delivered: 2,
    });
    expect(filtered.projects.find((project) => project.id === "alpha")).toMatchObject({
      claims: 2, inbound: 0, outbound: 2,
    });
    expect(filtered.projects.find((project) => project.id === "beta")).toMatchObject({
      claims: 1, inbound: 2, outbound: 0,
    });
  });

  it("combines health and query constraints without inventing adjacent traffic", () => {
    const filtered = filterCommunicationModel(MODEL, { query: "GAMMA", health: "failed" });
    expect(filtered.messages).toBe(1);
    expect(filtered.edges).toHaveLength(1);
    expect(filtered.nodes.map((node) => node.id).sort()).toEqual(["beta/two", "gamma/three"]);
    expect(filtered.nodes.find((node) => node.id === "gamma/three")).toMatchObject({
      messages: 1,
      inbound: 1,
      outbound: 0,
      failed: 1,
    });
  });

  it("retains a matching quiet identity only for the all-health projection", () => {
    const quiet = filterCommunicationModel(MODEL, { query: "observer", health: "all" });
    expect(quiet.edges).toEqual([]);
    expect(quiet.nodes).toMatchObject([{ id: "quiet/observer", messages: 0 }]);
    expect(quiet.projects).toMatchObject([{ id: "quiet", claims: 4 }]);
    expect(filterCommunicationModel(MODEL, { query: "observer", health: "failed" }).nodes).toEqual([]);
  });

  it("counts a retained self-route as both inbound and outbound", () => {
    const deferred = filterCommunicationModel(MODEL, { query: "", health: "deferred" });
    expect(deferred.nodes).toMatchObject([
      { id: "gamma/three", messages: 2, inbound: 1, outbound: 1, deferred: 2 },
    ]);
    expect(deferred.projects[0]).toMatchObject({ inbound: 1, outbound: 1, claims: 0 });
  });

  it("keeps sparse model endpoints honest and deterministically sorts exact ties", () => {
    const sparse = filterCommunicationModel(SPARSE_MODEL, { query: "", health: "unknown" });
    expect(sparse.edges).toHaveLength(3);
    expect(sparse.nodes.map((node) => node.id)).toEqual(["bare", "orphan/z", "same/a", "same/b"]);
    expect(sparse.projects.find((project) => project.id === "same")).toMatchObject({
      members: ["same/a", "same/b"],
      claims: 0,
    });
    expect(sparse.projects.map((project) => project.id)).toEqual(["same", "orphan", "unscoped"]);
    const bare = filterCommunicationModel(SPARSE_MODEL, { query: "bare", health: "all" });
    expect(bare.nodes.map((node) => node.id)).toEqual(["bare"]);
    expect(bare.projects[0]?.claims).toBe(3);
  });
});
