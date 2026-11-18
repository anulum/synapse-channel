// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — the cockpit scope's parsing, URL round trip and scoped requests

import { describe, expect, it } from "vitest";

import {
  FLEET_MIRROR,
  WHOLE_HUB,
  appendQuery,
  groupOptions,
  parseScope,
  peerScopes,
  sameScope,
  scopeFromSearch,
  scopeOptions,
  scopeText,
  scopeToSearch,
  withScope,
} from "../src/lib/scope";

const ALPHA = { kind: "project", namespace: "ALPHA" } as const;
const NONE = { kind: "none" } as const;
const EAST = { kind: "peer", hub: "east" } as const;
const SHIPS = { kind: "group", name: "ships" } as const;

describe("parseScope", () => {
  it("reads the three forms the dashboard serves", () => {
    expect(parseScope("all")).toEqual(WHOLE_HUB);
    expect(parseScope("none")).toEqual(NONE);
    expect(parseScope("project:ALPHA")).toEqual(ALPHA);
    expect(parseScope("project:with space")).toEqual({ kind: "project", namespace: "with space" });
    expect(parseScope("peer:east")).toEqual(EAST);
    expect(parseScope("group:ships")).toEqual(SHIPS);
    expect(scopeText(SHIPS)).toBe("group:ships");
  });

  it.each([
    null,
    "",
    "everything",
    "project:",
    "project:a/b",
    `project:${"x".repeat(257)}`,
    "project:tab\there",
    "project:non breaking",
    "peer:",
    "peer:a/b",
    "group:",
    "group:a/b",
    "elsewhere:ALPHA",
  ])("falls back to the whole hub for %j", (value) => {
    expect(parseScope(value)).toEqual(WHOLE_HUB);
  });

  it("accepts a namespace of exactly the longest length", () => {
    const namespace = "x".repeat(256);
    expect(parseScope(`project:${namespace}`)).toEqual({ kind: "project", namespace });
  });
});

describe("the URL", () => {
  it("round-trips every scope and keeps the other keys", () => {
    for (const scope of [WHOLE_HUB, NONE, ALPHA, SHIPS, EAST]) {
      const search = scopeToSearch(scope, "?panel=fleet&agent=ALPHA%2Fone");
      expect(scopeFromSearch(search)).toEqual(scope);
      expect(new URLSearchParams(search).get("panel")).toBe("fleet");
      expect(new URLSearchParams(search).get("agent")).toBe("ALPHA/one");
    }
  });

  it("omits the whole hub and replaces an earlier scope", () => {
    expect(scopeToSearch(WHOLE_HUB, "?scope=none")).toBe("");
    expect(scopeToSearch(ALPHA, "scope=none")).toBe("?scope=project%3AALPHA");
    expect(scopeFromSearch("")).toEqual(WHOLE_HUB);
  });
});

describe("scoped requests", () => {
  it("asks each endpoint for the scope, after its own query when it has one", () => {
    expect(withScope("/snapshot.json", WHOLE_HUB)).toBe("/snapshot.json");
    expect(withScope("/snapshot.json", NONE)).toBe("/snapshot.json?scope=none");
    expect(withScope("/events.json?since=4", ALPHA)).toBe("/events.json?since=4&scope=project%3AALPHA");
    expect(appendQuery("/waits.json", "")).toBe("/waits.json");
  });

  it("names each scope as the dashboard reads it and compares scopes by it", () => {
    expect([WHOLE_HUB, NONE, ALPHA, EAST].map(scopeText)).toEqual(["all", "none", "project:ALPHA", "peer:east"]);
    expect(withScope("/snapshot.json", EAST)).toBe("/snapshot.json?scope=peer%3Aeast");
    expect(sameScope(ALPHA, { kind: "project", namespace: "ALPHA" })).toBe(true);
    expect(sameScope(ALPHA, NONE)).toBe(false);
  });
});

describe("scopeOptions", () => {
  const counts = {
    tasksTotal: 7,
    agentsTotal: 4,
    tasksWithoutProject: 2,
    agentsWithoutNamespace: 1,
    namespaces: [
      { namespace: "ALPHA", tasks: 2, agents: 1 },
      { namespace: "BETA", tasks: 3, agents: 2 },
    ],
  };

  it("offers the whole hub, no project and each namespace with its counts", () => {
    expect(scopeOptions(counts, WHOLE_HUB)).toEqual([
      { scope: WHOLE_HUB, tasks: 7, agents: 4 },
      { scope: NONE, tasks: 2, agents: 1 },
      { scope: ALPHA, tasks: 2, agents: 1 },
      { scope: { kind: "project", namespace: "BETA" }, tasks: 3, agents: 2 },
    ]);
  });

  it("keeps the current scope as a choice when the hub no longer has it", () => {
    const gone = { kind: "project", namespace: "GONE" } as const;
    expect(scopeOptions(counts, gone).at(-1)).toEqual({ scope: gone, tasks: 0, agents: 0 });
  });

  it("keeps a peer out of the local choices and lists the observed hubs on their own", () => {
    const withPeers = { ...counts, peers: ["east", "west"] };
    expect(scopeOptions(withPeers, EAST).map((option) => scopeText(option.scope))).toEqual([
      "all",
      "none",
      "project:ALPHA",
      "project:BETA",
    ]);
    expect(peerScopes(withPeers, WHOLE_HUB)).toEqual([EAST, { kind: "peer", hub: "west" }]);
    expect(peerScopes(withPeers, EAST)).toEqual([EAST, { kind: "peer", hub: "west" }]);
    expect(peerScopes(counts, { kind: "peer", hub: "gone" })).toEqual([{ kind: "peer", hub: "gone" }]);
    expect(peerScopes(null, ALPHA)).toEqual([]);
  });

  it("reads the Fleet mirror's scope, writes it to the URL and keeps it out of the local choices", () => {
    expect(parseScope("fleet")).toEqual(FLEET_MIRROR);
    expect(scopeText(FLEET_MIRROR)).toBe("fleet");
    expect(scopeToSearch(FLEET_MIRROR, "?theme=dark")).toBe("?theme=dark&scope=fleet");
    expect(scopeFromSearch("?scope=fleet")).toEqual(FLEET_MIRROR);
    expect(sameScope(FLEET_MIRROR, { kind: "fleet" })).toBe(true);
    expect(scopeOptions(counts, FLEET_MIRROR).map((option) => scopeText(option.scope))).toEqual([
      "all",
      "none",
      "project:ALPHA",
      "project:BETA",
    ]);
    expect(peerScopes(counts, FLEET_MIRROR)).toEqual([]);
    expect(groupOptions(counts, FLEET_MIRROR)).toEqual(groupOptions(counts, WHOLE_HUB));
  });

  it("lists the declared groups on their own and keeps the current one", () => {
    const withGroups = {
      ...counts,
      groups: [
        { group: "ships", tasks: 5, agents: 3 },
        { group: "beta", tasks: 3, agents: 2 },
      ],
    };
    expect(groupOptions(withGroups, WHOLE_HUB)).toEqual([
      { scope: SHIPS, tasks: 5, agents: 3 },
      { scope: { kind: "group", name: "beta" }, tasks: 3, agents: 2 },
    ]);
    // A group never falls into the local choices, even when it is the current scope.
    expect(scopeOptions(withGroups, SHIPS)).toHaveLength(4);
    expect(groupOptions(withGroups, SHIPS)).toHaveLength(2);
    const gone = { kind: "group", name: "gone" } as const;
    expect(groupOptions(counts, gone)).toEqual([{ scope: gone, tasks: 0, agents: 0 }]);
    expect(groupOptions(null, ALPHA)).toEqual([]);
  });

  it("offers zero counts before the first snapshot", () => {
    expect(scopeOptions(null, ALPHA)).toEqual([
      { scope: WHOLE_HUB, tasks: 0, agents: 0 },
      { scope: NONE, tasks: 0, agents: 0 },
      { scope: ALPHA, tasks: 0, agents: 0 },
    ]);
  });
});
