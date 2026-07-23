// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — fleet communication layout tests

import { describe, expect, it } from "vitest";

import { layoutCommunicationWeb, matrixIdentities } from "../src/lib/communicationLayout";
import type { CommunicationModel, CommunicationNode } from "../src/lib/communicationModel";

function node(id: string, project: string, messages: number): CommunicationNode {
  return {
    id,
    project,
    exact: true,
    messages,
    inbound: messages,
    outbound: 0,
    delivered: 0,
    deferred: 0,
    failed: 0,
    lastTs: messages,
  };
}

function model(nodes: readonly CommunicationNode[]): CommunicationModel {
  return { nodes, edges: [], projects: [], messages: nodes.reduce((total, item) => total + item.messages, 0) };
}

describe("communication layout", () => {
  it("keeps project sectors deterministic and filters quiet roster members", () => {
    const input = model([
      node("beta/two", "beta", 4),
      node("alpha/two", "alpha", 2),
      node("quiet/one", "quiet", 0),
      node("alpha/one", "alpha", 3),
    ]);
    const first = layoutCommunicationWeb(input);
    expect(first).toEqual(layoutCommunicationWeb(input));
    expect(first.nodes.map((item) => item.id)).toEqual(["alpha/one", "alpha/two", "beta/two"]);
    expect(first.byId.get("alpha/one")).toBe(first.nodes[0]);
    expect(first.nodes.map((item) => item.colourIndex)).toEqual([0, 0, 1]);
  });

  it("honours graph bounds and both geometry radius limits", () => {
    const nodes = Array.from({ length: 15 }, (_, index) => node(`p/a${index}`, "p", index === 0 ? 100 : 1));
    const input = model(nodes);
    expect(layoutCommunicationWeb(input, 760, 360, 8).nodes).toHaveLength(8);
    expect(layoutCommunicationWeb(input, 100, 100, 0).nodes).toHaveLength(1);
    expect(layoutCommunicationWeb(input, 100, 100, 0).nodes[0]?.radius).toBe(10);
    expect(layoutCommunicationWeb(model([])).nodes).toEqual([]);
  });

  it("returns the busiest active identities within the matrix bound", () => {
    const input = model([
      node("p/one", "p", 3),
      node("p/two", "p", 2),
      node("p/quiet", "p", 0),
    ]);
    expect(matrixIdentities(input)).toHaveLength(2);
    expect(matrixIdentities(input, 0).map((item) => item.id)).toEqual(["p/one"]);
  });
});
