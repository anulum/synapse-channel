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

const LIVE_REPLAY = { mode: "live" } as const;
const DEFAULT_COMMUNICATION = { communicationQuery: "", communicationHealth: "all" } as const;

describe("workspaceFromSearch", () => {
  it("uses stable defaults for absent or unknown navigation values", () => {
    expect(workspaceFromSearch("")).toEqual(DEFAULT_WORKSPACE);
    expect(workspaceFromSearch("?panel=unknown&fleet=unknown")).toEqual(DEFAULT_WORKSPACE);
  });

  it("keeps selection independent from the active panel", () => {
    expect(workspaceFromSearch("?panel=attention&agent=alpha%2Fone")).toEqual({
      panel: "attention",
      fleetView: "web",
      selection: { kind: "agent", id: "alpha/one" },
      replay: LIVE_REPLAY,
      incidentStep: "scope",
      ...DEFAULT_COMMUNICATION,
    });
  });

  it("parses every supported selection without decoding an arbitrary object", () => {
    expect(workspaceFromSearch("panel=fleet&agent=alpha%2Fone")).toEqual({
      panel: "fleet",
      fleetView: "web",
      selection: { kind: "agent", id: "alpha/one" },
      replay: LIVE_REPLAY,
      incidentStep: "scope",
      ...DEFAULT_COMMUNICATION,
    });
    expect(workspaceFromSearch("?panel=fleet&fleet=projects&project=SYNAPSE-CHANNEL")).toEqual({
      panel: "fleet",
      fleetView: "projects",
      selection: { kind: "project", id: "SYNAPSE-CHANNEL" },
      replay: LIVE_REPLAY,
      incidentStep: "scope",
      ...DEFAULT_COMMUNICATION,
    });
    expect(workspaceFromSearch("?panel=fleet&fleet=matrix&from=alpha%2Fone&to=beta%2Ftwo")).toEqual({
      panel: "fleet",
      fleetView: "matrix",
      selection: { kind: "route", source: "alpha/one", target: "beta/two" },
      replay: LIVE_REPLAY,
      incidentStep: "scope",
      ...DEFAULT_COMMUNICATION,
    });
    expect(workspaceFromSearch("?panel=log&task=SCH-17").selection).toEqual({
      kind: "task",
      id: "SCH-17",
    });
    expect(workspaceFromSearch("?panel=log&event=429").selection).toEqual({
      kind: "event",
      seq: 429,
    });
    expect(workspaceFromSearch("?panel=fleet&fleet=timeline&event=430")).toEqual({
      panel: "fleet",
      fleetView: "timeline",
      selection: { kind: "event", seq: 430 },
      replay: LIVE_REPLAY,
      incidentStep: "scope",
      ...DEFAULT_COMMUNICATION,
    });
    expect(workspaceFromSearch("?panel=fleet&fleet=flow&project=alpha")).toEqual({
      panel: "fleet",
      fleetView: "flow",
      selection: { kind: "project", id: "alpha" },
      replay: LIVE_REPLAY,
      incidentStep: "scope",
      ...DEFAULT_COMMUNICATION,
    });
  });

  it("ignores partial, empty, oversized, controlled, and invalid event selections", () => {
    const oversized = "x".repeat(513);
    expect(workspaceFromSearch("?panel=fleet&from=alpha&to=&agent=beta").selection).toEqual({
      kind: "agent",
      id: "beta",
    });
    expect(workspaceFromSearch(`?panel=fleet&agent=${oversized}`).selection).toBeNull();
    expect(workspaceFromSearch("?panel=fleet&project=%0Aunsafe").selection).toBeNull();
    expect(workspaceFromSearch("?panel=audit&event=-1").selection).toBeNull();
    expect(workspaceFromSearch("?panel=audit&event=1.5").selection).toBeNull();
    expect(workspaceFromSearch(`?panel=audit&event=${Number.MAX_SAFE_INTEGER}0`).selection).toBeNull();
    expect(workspaceFromSearch("?panel=fleet").selection).toBeNull();
  });

  it("parses only complete bounded replay modes", () => {
    expect(workspaceFromSearch("?replay=history&at=42").replay).toEqual({ mode: "history", at: 42 });
    expect(workspaceFromSearch("?replay=compare&a=10&b=20").replay).toEqual({
      mode: "compare",
      a: 10,
      b: 20,
    });
    expect(workspaceFromSearch("?replay=history&at=-1").replay).toEqual(LIVE_REPLAY);
    expect(workspaceFromSearch("?replay=compare&a=10").replay).toEqual(LIVE_REPLAY);
    expect(workspaceFromSearch("?replay=compare&a=1.5&b=20").replay).toEqual(LIVE_REPLAY);
    expect(workspaceFromSearch(`?replay=history&at=${Number.MAX_SAFE_INTEGER}0`).replay).toEqual(LIVE_REPLAY);
  });

  it("parses a bounded incident step only through the incident panel", () => {
    expect(workspaceFromSearch("?panel=incident&incident=evidence")).toMatchObject({
      panel: "incident",
      incidentStep: "evidence",
    });
    expect(workspaceFromSearch("?panel=incident&incident=unknown")).toMatchObject({
      panel: "incident",
      incidentStep: "scope",
    });
  });

  it("parses bounded communication filters and rejects malformed values", () => {
    expect(workspaceFromSearch("?panel=fleet&comm=ALPHA%2Fone&delivery=failed")).toMatchObject({
      communicationQuery: "ALPHA/one",
      communicationHealth: "failed",
    });
    expect(workspaceFromSearch("?panel=fleet&comm=%0Aunsafe&delivery=invented")).toMatchObject(
      DEFAULT_COMMUNICATION,
    );
    expect(workspaceFromSearch(`?panel=fleet&comm=${"x".repeat(121)}`)).toMatchObject(
      DEFAULT_COMMUNICATION,
    );
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
    const audit: CockpitWorkspace = {
      panel: "audit",
      fleetView: "projects",
      selection: null,
      replay: LIVE_REPLAY,
      incidentStep: "scope",
      ...DEFAULT_COMMUNICATION,
    };
    expect(workspaceToSearch(audit)).toBe("?panel=audit");
    expect(
      workspaceToSearch({
        panel: "attention",
        fleetView: "web",
        selection: null,
        replay: LIVE_REPLAY,
        incidentStep: "scope",
        ...DEFAULT_COMMUNICATION,
      }),
    ).toBe("?panel=attention");

    expect(
      workspaceToSearch({
        panel: "audit",
        fleetView: "web",
        selection: { kind: "agent", id: "alpha/one" },
        replay: LIVE_REPLAY,
        incidentStep: "scope",
        ...DEFAULT_COMMUNICATION,
      }),
    ).toBe("?panel=audit&agent=alpha%2Fone");
    expect(
      workspaceToSearch({
        panel: "fleet",
        fleetView: "projects",
        selection: { kind: "project", id: "SYNAPSE CHANNEL" },
        replay: LIVE_REPLAY,
        incidentStep: "scope",
        ...DEFAULT_COMMUNICATION,
      }),
    ).toBe("?panel=fleet&fleet=projects&project=SYNAPSE+CHANNEL");
    expect(
      workspaceToSearch({
        panel: "fleet",
        fleetView: "matrix",
        selection: { kind: "route", source: "alpha/one", target: "beta/two" },
        replay: LIVE_REPLAY,
        incidentStep: "scope",
        ...DEFAULT_COMMUNICATION,
      }),
    ).toBe("?panel=fleet&fleet=matrix&from=alpha%2Fone&to=beta%2Ftwo");
    expect(
      workspaceToSearch({
        panel: "fleet",
        fleetView: "web",
        selection: null,
        replay: LIVE_REPLAY,
        incidentStep: "scope",
        ...DEFAULT_COMMUNICATION,
      }, "lang=en"),
    ).toBe("?lang=en&panel=fleet");
    expect(
      workspaceToSearch({
        panel: "log",
        fleetView: "web",
        selection: { kind: "task", id: "SCH-17" },
        replay: LIVE_REPLAY,
        incidentStep: "scope",
        ...DEFAULT_COMMUNICATION,
      }),
    ).toBe("?task=SCH-17");
    expect(
      workspaceToSearch({
        panel: "log",
        fleetView: "web",
        selection: { kind: "event", seq: 429 },
        replay: LIVE_REPLAY,
        incidentStep: "scope",
        ...DEFAULT_COMMUNICATION,
      }),
    ).toBe("?event=429");
  });

  it("serialises only a non-default incident step while that panel is active", () => {
    expect(workspaceToSearch({
      panel: "incident",
      fleetView: "web",
      selection: { kind: "event", seq: 44 },
      replay: LIVE_REPLAY,
      incidentStep: "evidence",
      ...DEFAULT_COMMUNICATION,
    })).toBe("?panel=incident&incident=evidence&event=44");
    expect(workspaceToSearch({
      panel: "log",
      fleetView: "web",
      selection: null,
      replay: LIVE_REPLAY,
      incidentStep: "notes",
      ...DEFAULT_COMMUNICATION,
    })).toBe("");
  });

  it("serialises communication filters only while the fleet panel is active", () => {
    expect(workspaceToSearch({
      ...DEFAULT_WORKSPACE,
      panel: "fleet",
      communicationQuery: "alpha/one",
      communicationHealth: "failed",
    })).toBe("?panel=fleet&comm=alpha%2Fone&delivery=failed");
    expect(workspaceToSearch({
      ...DEFAULT_WORKSPACE,
      panel: "audit",
      communicationQuery: "alpha/one",
      communicationHealth: "failed",
    })).toBe("?panel=audit");
    expect(workspaceToSearch({
      ...DEFAULT_WORKSPACE,
      panel: "fleet",
      communicationQuery: ` ${"x".repeat(130)} `,
    })).toBe(`?panel=fleet&comm=${"x".repeat(120)}`);
    expect(workspaceToSearch({
      ...DEFAULT_WORKSPACE,
      panel: "fleet",
      communicationQuery: "unsafe\nquery",
    })).toBe("?panel=fleet");
  });

  it("round-trips a shareable fleet route", () => {
    const workspace: CockpitWorkspace = {
      panel: "fleet",
      fleetView: "matrix",
      selection: { kind: "route", source: "alpha/one", target: "beta/two" },
      replay: LIVE_REPLAY,
      incidentStep: "scope",
      ...DEFAULT_COMMUNICATION,
    };
    expect(workspaceFromSearch(workspaceToSearch(workspace))).toEqual(workspace);
  });

  it("round-trips history and comparison without retaining stale replay fields", () => {
    const historyWorkspace: CockpitWorkspace = {
      panel: "log",
      fleetView: "web",
      selection: { kind: "event", seq: 44 },
      replay: { mode: "history", at: 42 },
      incidentStep: "scope",
      ...DEFAULT_COMMUNICATION,
    };
    expect(workspaceToSearch(historyWorkspace, "?a=1&b=2&lang=sk")).toBe(
      "?lang=sk&event=44&replay=history&at=42",
    );
    expect(workspaceFromSearch(workspaceToSearch(historyWorkspace))).toEqual(historyWorkspace);

    const compareWorkspace: CockpitWorkspace = {
      ...historyWorkspace,
      replay: { mode: "compare", a: 10, b: 42 },
      incidentStep: "scope",
    };
    expect(workspaceFromSearch(workspaceToSearch(compareWorkspace))).toEqual(compareWorkspace);
  });
});
