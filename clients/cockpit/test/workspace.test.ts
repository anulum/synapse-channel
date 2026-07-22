// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — cockpit workspace URL contract tests

import { describe, expect, it } from "vitest";

import {
  DEFAULT_WORKSPACE,
  workspaceFromSearch,
  workspaceToSearch,
  type CockpitWorkspace,
} from "../src/lib/workspace";

describe("workspaceFromSearch", () => {
  it("uses stable defaults for absent or unknown navigation values", () => {
    expect(workspaceFromSearch("")).toEqual(DEFAULT_WORKSPACE);
    expect(workspaceFromSearch("?panel=unknown&fleet=timeline&agent=ignored")).toEqual(
      DEFAULT_WORKSPACE,
    );
  });

  it("parses every fleet selection without decoding an arbitrary object", () => {
    expect(workspaceFromSearch("panel=fleet&agent=alpha%2Fone")).toEqual({
      panel: "fleet",
      fleetView: "web",
      selection: { kind: "agent", id: "alpha/one" },
    });
    expect(workspaceFromSearch("?panel=fleet&fleet=projects&project=SYNAPSE-CHANNEL")).toEqual({
      panel: "fleet",
      fleetView: "projects",
      selection: { kind: "project", id: "SYNAPSE-CHANNEL" },
    });
    expect(workspaceFromSearch("?panel=fleet&fleet=matrix&from=alpha%2Fone&to=beta%2Ftwo")).toEqual({
      panel: "fleet",
      fleetView: "matrix",
      selection: { kind: "route", source: "alpha/one", target: "beta/two" },
    });
  });

  it("ignores partial, empty, oversized, controlled, and off-panel selections", () => {
    const oversized = "x".repeat(513);
    expect(workspaceFromSearch("?panel=fleet&from=alpha&to=&agent=beta").selection).toEqual({
      kind: "agent",
      id: "beta",
    });
    expect(workspaceFromSearch(`?panel=fleet&agent=${oversized}`).selection).toBeNull();
    expect(workspaceFromSearch("?panel=fleet&project=%0Aunsafe").selection).toBeNull();
    expect(workspaceFromSearch("?panel=audit&agent=alpha").selection).toBeNull();
    expect(workspaceFromSearch("?panel=fleet").selection).toBeNull();
  });
});

describe("workspaceToSearch", () => {
  it("omits defaults, removes stale workspace fields, and preserves unrelated parameters", () => {
    expect(workspaceToSearch(DEFAULT_WORKSPACE)).toBe("");
    expect(
      workspaceToSearch(DEFAULT_WORKSPACE, "?lang=sk&panel=fleet&fleet=matrix&from=a&to=b"),
    ).toBe("?lang=sk");
  });

  it("serialises panels, fleet modes, and each bounded selection", () => {
    const audit: CockpitWorkspace = { panel: "audit", fleetView: "projects", selection: null };
    expect(workspaceToSearch(audit)).toBe("?panel=audit");

    expect(
      workspaceToSearch({
        panel: "fleet",
        fleetView: "web",
        selection: { kind: "agent", id: "alpha/one" },
      }),
    ).toBe("?panel=fleet&agent=alpha%2Fone");
    expect(
      workspaceToSearch({
        panel: "fleet",
        fleetView: "projects",
        selection: { kind: "project", id: "SYNAPSE CHANNEL" },
      }),
    ).toBe("?panel=fleet&fleet=projects&project=SYNAPSE+CHANNEL");
    expect(
      workspaceToSearch({
        panel: "fleet",
        fleetView: "matrix",
        selection: { kind: "route", source: "alpha/one", target: "beta/two" },
      }),
    ).toBe("?panel=fleet&fleet=matrix&from=alpha%2Fone&to=beta%2Ftwo");
    expect(
      workspaceToSearch({ panel: "fleet", fleetView: "web", selection: null }, "lang=en"),
    ).toBe("?lang=en&panel=fleet");
  });

  it("round-trips a shareable fleet route", () => {
    const workspace: CockpitWorkspace = {
      panel: "fleet",
      fleetView: "matrix",
      selection: { kind: "route", source: "alpha/one", target: "beta/two" },
    };
    expect(workspaceFromSearch(workspaceToSearch(workspace))).toEqual(workspace);
  });
});
