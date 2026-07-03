// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — signal-log query filtering and URL round-trip tests

import { describe, expect, it } from "vitest";
import {
  applyQuery,
  isConstrained,
  matchesQuery,
  OPEN_QUERY,
  queryFromHash,
  queryToHash,
  type LogQuery,
} from "../src/lib/logQuery";
import type { CockpitEvent } from "../src/types";

function event(seq: number, overrides: Partial<CockpitEvent> = {}): CockpitEvent {
  return {
    seq,
    ts: seq,
    kind: "claim",
    lane: "claims",
    severity: 0.5,
    actor: "ATLAS/worker-1",
    label: "claimed ATLAS-7",
    taskId: "ATLAS-7",
    ...overrides,
  };
}

describe("matchesQuery / applyQuery", () => {
  const events = [
    event(3, { kind: "conflict", actor: "", label: "branch_conflict: VEGA", taskId: "" }),
    event(2, { kind: "chat", actor: "LYRA/docs", label: "docs drafted", taskId: "" }),
    event(1),
  ];

  it("matches case-insensitively across label, actor, task id, and kind", () => {
    expect(matchesQuery(events[2] as CockpitEvent, { ...OPEN_QUERY, text: "atlas-7" })).toBe(true);
    expect(matchesQuery(events[2] as CockpitEvent, { ...OPEN_QUERY, text: "WORKER-1" })).toBe(true);
    expect(matchesQuery(events[1] as CockpitEvent, { ...OPEN_QUERY, text: "drafted" })).toBe(true);
    expect(matchesQuery(events[0] as CockpitEvent, { ...OPEN_QUERY, text: "confl" })).toBe(true);
    expect(matchesQuery(events[1] as CockpitEvent, { ...OPEN_QUERY, text: "nowhere" })).toBe(false);
  });

  it("filters by kind subset and keeps everything on null", () => {
    const onlyChat: LogQuery = { ...OPEN_QUERY, kinds: ["chat"] };
    expect(applyQuery(events, onlyChat).map((item) => item.seq)).toEqual([2]);
    expect(applyQuery(events, OPEN_QUERY)).toHaveLength(3);
  });

  it("orders oldest-first on request without mutating the input", () => {
    const oldest: LogQuery = { ...OPEN_QUERY, order: "oldest" };
    expect(applyQuery(events, oldest).map((item) => item.seq)).toEqual([1, 2, 3]);
    expect(events.map((item) => item.seq)).toEqual([3, 2, 1]);
  });

  it("combines text and kind constraints", () => {
    const query: LogQuery = { text: "vega", kinds: ["conflict"], order: "newest" };
    expect(applyQuery(events, query).map((item) => item.seq)).toEqual([3]);
    expect(applyQuery(events, { ...query, kinds: ["chat"] })).toEqual([]);
  });
});

describe("isConstrained", () => {
  it("is false only for the open query", () => {
    expect(isConstrained(OPEN_QUERY)).toBe(false);
    expect(isConstrained({ ...OPEN_QUERY, text: " x " })).toBe(true);
    expect(isConstrained({ ...OPEN_QUERY, kinds: [] })).toBe(true);
    expect(isConstrained({ ...OPEN_QUERY, order: "oldest" })).toBe(true);
  });
});

describe("URL hash round-trip", () => {
  it("serialises only non-defaults and round-trips", () => {
    expect(queryToHash(OPEN_QUERY)).toBe("");
    const query: LogQuery = { text: "vega kernel", kinds: ["claim", "conflict"], order: "oldest" };
    const hash = queryToHash(query);
    expect(hash).toContain("q=vega+kernel");
    expect(queryFromHash(`#${hash}`)).toEqual(query);
    expect(queryFromHash("")).toEqual(OPEN_QUERY);
  });

  it("drops unknown kinds and never yields an accidentally-empty kind filter", () => {
    expect(queryFromHash("#kinds=claim,junk").kinds).toEqual(["claim"]);
    expect(queryFromHash("#kinds=junk,also-junk").kinds).toBeNull();
    expect(queryFromHash("#order=sideways").order).toBe("newest");
  });
});
