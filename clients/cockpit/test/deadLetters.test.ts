// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — dead-letter parsing tests

import { describe, expect, it } from "vitest";
import { parseDeadLetters } from "../src/lib/deadLetters";
import { parseSnapshot } from "../src/lib/snapshot";
import type { FleetSnapshot } from "../src/types";

function snapshotWith(deadLetters: unknown): FleetSnapshot {
  const parsed = parseSnapshot({ state: { dead_letters: deadLetters } });
  if (parsed === null) throw new Error("fixture did not parse");
  return parsed;
}

describe("parseDeadLetters", () => {
  it("reads the hub's records newest first", () => {
    const letters = parseDeadLetters(
      snapshotWith([
        { target: "CEO", count: 2, last_sender: "a/say", last_ts: 100 },
        { target: "SYNAPSE-CHANNEL/coordinator", count: 5, last_sender: "b/tx", last_ts: 300 },
      ]),
    );
    expect(letters.map((letter) => letter.target)).toEqual([
      "SYNAPSE-CHANNEL/coordinator",
      "CEO",
    ]);
    expect(letters[0]).toEqual({
      target: "SYNAPSE-CHANNEL/coordinator",
      count: 5,
      lastSender: "b/tx",
      lastTs: 300,
    });
  });

  it("tolerates malformed entries, missing fields, and pre-0.95 hubs", () => {
    expect(parseDeadLetters(null)).toEqual([]);
    expect(parseDeadLetters(snapshotWith(undefined))).toEqual([]);
    expect(parseDeadLetters(snapshotWith("junk"))).toEqual([]);
    const letters = parseDeadLetters(snapshotWith([{ target: "x" }, "junk", 42]));
    expect(letters).toEqual([{ target: "x", count: 0, lastSender: "", lastTs: null }]);
  });

  it("breaks equal-time ties by target and treats a missing ts as oldest", () => {
    const letters = parseDeadLetters(
      snapshotWith([
        { target: "beta", count: 1, last_ts: 10 },
        { target: "alfa", count: 1, last_ts: 10 },
        { target: "stampless-b", count: 1 },
        { target: "stampless-a", count: 1 },
      ]),
    );
    expect(letters.map((letter) => letter.target)).toEqual([
      "alfa",
      "beta",
      "stampless-a",
      "stampless-b",
    ]);
  });
});
