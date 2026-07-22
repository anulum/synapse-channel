// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — retained event-window coverage tests

import { describe, expect, it } from "vitest";

import {
  EVENT_RETENTION_LIMIT,
  eventCoverageOf,
} from "../src/lib/eventCoverage";
import type { CockpitEvent } from "../src/types";

function event(seq: number, ts = seq): CockpitEvent {
  return {
    seq,
    ts,
    kind: "chat",
    lane: "task",
    severity: 0.2,
    actor: "alpha/one",
    label: `event ${seq}`,
    taskId: "",
  };
}

describe("eventCoverageOf", () => {
  it("reports an empty bounded connecting window without invented ranges", () => {
    expect(eventCoverageOf([], "connecting")).toEqual({
      source: "connecting",
      retained: 0,
      capacity: EVENT_RETENTION_LIMIT,
      minSeq: null,
      maxSeq: null,
      minTs: null,
      maxTs: null,
      atCapacity: false,
    });
  });

  it("finds sequence and time bounds independently of input order", () => {
    expect(eventCoverageOf([event(9, 90), event(4, 120), event(7, 60)], "hub")).toEqual({
      source: "hub",
      retained: 3,
      capacity: EVENT_RETENTION_LIMIT,
      minSeq: 4,
      maxSeq: 9,
      minTs: 60,
      maxTs: 120,
      atCapacity: false,
    });
  });

  it("states only that a derived retained window reached the client cap", () => {
    const events = Array.from({ length: EVENT_RETENTION_LIMIT }, (_, index) => event(index + 1));
    const coverage = eventCoverageOf(events, "derived");
    expect(coverage.retained).toBe(EVENT_RETENTION_LIMIT);
    expect(coverage.atCapacity).toBe(true);
    expect(coverage.minSeq).toBe(1);
    expect(coverage.maxSeq).toBe(EVENT_RETENTION_LIMIT);
  });
});
