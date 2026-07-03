// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — causality-trace parsing, clustering, and query tests

import { describe, expect, it, vi } from "vitest";
import {
  clusterByHub,
  fetchTrace,
  parseTrace,
  traceUrl,
  type CausalityNode,
} from "../src/lib/causality";

function node(seq: number, overrides: Partial<CausalityNode> = {}): CausalityNode {
  return {
    seq,
    kind: "claim",
    owner: "a",
    taskId: "t",
    ts: 100,
    status: "claimed",
    text: "",
    worktree: "",
    hubId: "",
    paths: [],
    dependsOn: [],
    ...overrides,
  };
}

describe("parseTrace", () => {
  it("parses the CLI causes shape verbatim", () => {
    const trace = parseTrace({
      direction: "causes",
      seq: 5200,
      present: true,
      node: {
        seq: 5200,
        kind: "release",
        owner: "",
        task_id: "REPO:git",
        ts: 1783037046.5,
        status: "",
        text: "",
        worktree: "",
        paths: [],
        depends_on: [],
      },
      direct: [
        {
          src: 5199,
          dst: 5200,
          relation: "lifecycle",
          detail: "claim → release",
          node: {
            seq: 5199,
            kind: "claim",
            owner: "agent/x",
            task_id: "REPO:git",
            ts: 1783037045.2,
            status: "claimed",
            text: "",
            worktree: "REPO:git",
            paths: ["src"],
            depends_on: ["dep-1"],
          },
        },
      ],
      transitive: [{ seq: 143, kind: "claim", owner: "op", task_id: "OTHER:git", ts: 1.5 }],
    });
    expect(trace?.present).toBe(true);
    expect(trace?.node?.kind).toBe("release");
    expect(trace?.direct).toHaveLength(1);
    expect(trace?.direct[0]).toMatchObject({
      src: 5199,
      dst: 5200,
      relation: "lifecycle",
      detail: "claim → release",
    });
    expect(trace?.direct[0]?.node.paths).toEqual(["src"]);
    expect(trace?.direct[0]?.node.dependsOn).toEqual(["dep-1"]);
    expect(trace?.transitive[0]).toMatchObject({ seq: 143, owner: "op", taskId: "OTHER:git" });
  });

  it("rejects non-objects and tolerates a partial or absent payload", () => {
    expect(parseTrace(null)).toBeNull();
    expect(parseTrace([])).toBeNull();
    const missing = parseTrace({ direction: "causes", seq: 9, present: false, note: "no event" });
    expect(missing).toEqual({
      direction: "causes",
      seq: 9,
      present: false,
      node: null,
      direct: [],
      transitive: [],
      note: "no event",
    });
    const junk = parseTrace({ direct: "junk", transitive: "junk", node: "junk", present: "yes" });
    expect(junk?.node).toBeNull();
    expect(junk?.present).toBe(false);
    expect(junk?.direct).toEqual([]);
    const nodeless = parseTrace({ present: true, direct: [{ src: 1, dst: 2 }] });
    expect(nodeless?.direct[0]?.node).toMatchObject({ seq: 0, kind: "", owner: "" });
  });
});

describe("clusterByHub", () => {
  it("groups federated nodes per hub, sorted by hub id", () => {
    const clusters = clusterByHub([
      node(1, { hubId: "hub-b" }),
      node(2, { hubId: "hub-a" }),
      node(3, { hubId: "hub-b" }),
      node(4),
    ]);
    expect(clusters.map((cluster) => [cluster.hubId, cluster.nodes.map((n) => n.seq)])).toEqual([
      ["", [4]],
      ["hub-a", [2]],
      ["hub-b", [1, 3]],
    ]);
  });
});

describe("traceUrl", () => {
  it("sends digits as seq and anything else as a task id", () => {
    expect(traceUrl({ subject: " 5200 ", direction: "causes" })).toBe(
      "/causality.json?seq=5200&direction=causes",
    );
    expect(traceUrl({ subject: "REPO:git", direction: "effects" })).toBe(
      "/causality.json?task=REPO%3Agit&direction=effects",
    );
  });
});

describe("fetchTrace", () => {
  const query = { subject: "1", direction: "causes" as const };

  it("returns loaded on a valid payload", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(JSON.stringify({ present: true, seq: 1 })));
    const result = await fetchTrace(query, fetcher);
    expect(result.kind).toBe("loaded");
    if (result.kind === "loaded") expect(result.trace.present).toBe(true);
  });

  it("maps 404 to absent, other statuses and bad payloads to error", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("no", { status: 404 }))
      .mockResolvedValueOnce(new Response("boom", { status: 500 }))
      .mockResolvedValueOnce(new Response(JSON.stringify([1])));
    expect(await fetchTrace(query, fetcher)).toEqual({ kind: "absent" });
    expect(await fetchTrace(query, fetcher)).toEqual({
      kind: "error",
      message: "hub returned 500",
    });
    expect(await fetchTrace(query, fetcher)).toEqual({
      kind: "error",
      message: "causality payload was not an object",
    });
  });

  it("carries a thrown reason, Error or not", async () => {
    const failing = vi.fn<typeof fetch>().mockRejectedValue(new Error("torn down"));
    expect(await fetchTrace(query, failing)).toEqual({ kind: "error", message: "torn down" });
    const stringy = vi.fn<typeof fetch>().mockRejectedValue("plain reason");
    expect(await fetchTrace(query, stringy)).toEqual({ kind: "error", message: "plain reason" });
  });

  it("uses the global fetch by default, which cannot resolve the relative URL in tests", async () => {
    const result = await fetchTrace(query);
    expect(result.kind).toBe("error");
  });
});
