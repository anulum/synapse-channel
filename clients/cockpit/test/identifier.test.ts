// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — identifier splitting contract

import { describe, expect, it } from "vitest";

import { splitIdentifier } from "../src/lib/identifier";

describe("splitIdentifier", () => {
  it("leaves a short value whole", () => {
    expect(splitIdentifier("")).toEqual({ head: "", tail: "" });
    expect(splitIdentifier("api")).toEqual({ head: "api", tail: "" });
    expect(splitIdentifier("LIGHT-20")).toEqual({ head: "LIGHT-20", tail: "" });
  });

  it("keeps the last segment of a seat, a task and a path as the tail", () => {
    expect(splitIdentifier("harbour-coastal-monitoring-programme/autonomous-operations-seat-api")).toEqual({
      head: "harbour-coastal-monitoring-programme/autonomous-operations-seat",
      tail: "-api",
    });
    expect(splitIdentifier("HARBOUR-COASTAL-OPERATIONS-WORK-PACKAGE-101")).toEqual({
      head: "HARBOUR-COASTAL-OPERATIONS-WORK-PACKAGE",
      tail: "-101",
    });
    expect(splitIdentifier("src/api/upload.py")).toEqual({ head: "src/api", tail: "/upload.py" });
  });

  it("takes the shortest end of at least four characters, separator included", () => {
    expect(splitIdentifier("harbour/api")).toEqual({ head: "harbour", tail: "/api" });
    // "-b" is too short to tell two seats apart; the cut moves to the next separator.
    expect(splitIdentifier("coastal/seat-a-b")).toEqual({ head: "coastal/seat", tail: "-a-b" });
  });

  it("falls back to the last six characters when no separator bounds a usable end", () => {
    expect(splitIdentifier("abcdefghijklmnop")).toEqual({ head: "abcdefghij", tail: "klmnop" });
    // The only separator leaves an end longer than sixteen characters.
    expect(splitIdentifier("ab/cdefghijklmnopqrstuvwxyz")).toEqual({
      head: "ab/cdefghijklmnopqrst",
      tail: "uvwxyz",
    });
    // A separator in the very last places is not an end.
    expect(splitIdentifier("abcdefghijkl-m")).toEqual({ head: "abcdefgh", tail: "ijkl-m" });
  });

  it("returns parts that always join to the value", () => {
    for (const value of ["a/b", "harbour/web", "x".repeat(40), "A-B-C-D-E-F-G-H-I", "é/ü-ß-ø-æ-å-ñ"]) {
      const { head, tail } = splitIdentifier(value);
      expect(head + tail).toBe(value);
    }
  });
});
