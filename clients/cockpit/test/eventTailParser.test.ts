// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — defensive event-tail parsing contracts

import { describe, expect, it } from "vitest";

import { parseStoredEvent, parseTail } from "../src/lib/eventTailParser";

describe("event-tail parsing", () => {
  it("parses a wire event tolerantly and drops malformed entries", () => {
    expect(parseStoredEvent({ seq: 7, ts: 1.5, kind: "claim", payload: { a: 1 } })).toEqual({
      seq: 7,
      ts: 1.5,
      kind: "claim",
      payload: { a: 1 },
    });
    expect(parseStoredEvent("junk")).toEqual({ seq: 0, ts: 0, kind: "", payload: {} });
    const tail = parseTail({
      events: [{ seq: 3, ts: 1, kind: "chat", payload: {} }, "junk"],
      next_cursor: 3,
    });
    expect(tail?.events).toHaveLength(1);
    expect(tail?.nextCursor).toBe(3);
    expect(tail?.historyIncluded).toBe(false);
    expect(
      parseTail({ events: [], next_cursor: 3, history_included: true })?.historyIncluded,
    ).toBe(true);
    expect(parseTail({ events: "junk" })).toEqual({
      events: [],
      nextCursor: 0,
      historyIncluded: false,
    });
    expect(parseTail(null)).toBeNull();
    expect(parseTail([1])).toBeNull();
  });
});
