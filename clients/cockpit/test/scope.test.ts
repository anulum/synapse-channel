// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — the cockpit scope's parsing, URL round trip and scoped requests

import { describe, expect, it } from "vitest";

import {
  WHOLE_HUB,
  appendQuery,
  parseScope,
  sameScope,
  scopeFromSearch,
  scopeOptions,
  scopeText,
  scopeToSearch,
  withScope,
} from "../src/lib/scope";

const ALPHA = { kind: "project", namespace: "ALPHA" } as const;
const NONE = { kind: "none" } as const;

describe("parseScope", () => {
  it("reads the three forms the dashboard serves", () => {
    expect(parseScope("all")).toEqual(WHOLE_HUB);
    expect(parseScope("none")).toEqual(NONE);
    expect(parseScope("project:ALPHA")).toEqual(ALPHA);
    expect(parseScope("project:with space")).toEqual({ kind: "project", namespace: "with space" });
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
    for (const scope of [WHOLE_HUB, NONE, ALPHA]) {
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
    expect([WHOLE_HUB, NONE, ALPHA].map(scopeText)).toEqual(["all", "none", "project:ALPHA"]);
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

  it("offers zero counts before the first snapshot", () => {
    expect(scopeOptions(null, ALPHA)).toEqual([
      { scope: WHOLE_HUB, tasks: 0, agents: 0 },
      { scope: NONE, tasks: 0, agents: 0 },
      { scope: ALPHA, tasks: 0, agents: 0 },
    ]);
  });
});
