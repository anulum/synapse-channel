// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — cockpit headline metric projection contracts

import { describe, expect, it } from "vitest";

import {
  cockpitStamp,
  headlineKpis,
  headlineMetricsOf,
  ZERO_HEADLINE_METRICS,
} from "../src/lib/cockpitKpis";
import { parseSnapshot, type SnapshotState } from "../src/lib/snapshot";
import type { CockpitEvent } from "../src/types";

function event(seq: number, ts: number): CockpitEvent {
  return {
    seq,
    ts,
    kind: "chat",
    lane: "task",
    severity: 0.4,
    actor: "operator/test",
    label: `event ${seq}`,
    taskId: "",
  };
}

describe("headlineMetricsOf", () => {
  it("projects live fleet counts and only the retained trailing minute", () => {
    const state: SnapshotState = {
      snapshot: parseSnapshot({
        fleet: {
          agents: { live: ["alpha", "beta"] },
          claims: { active: 3 },
        },
        risk: {
          signals: [
            { level: "red", category: "conflict", subject: "T-1", detail: "overlap" },
            { level: "amber", category: "wait", subject: "T-2", detail: "gated" },
            { level: "red", category: "stale", subject: "T-3", detail: "expired" },
          ],
        },
      }),
      status: "live",
      fetchedAt: 20_000,
      error: null,
    };

    expect(headlineMetricsOf(state, [event(3, 100), event(2, 41), event(1, 39)], 100_000)).toEqual({
      agents: 2,
      claims: 3,
      risk: 2,
      ratePerMinute: 2,
    });
  });

  it("retains an observed rate while the first snapshot is unavailable", () => {
    const state: SnapshotState = {
      snapshot: null,
      status: "connecting",
      fetchedAt: null,
      error: null,
    };

    expect(headlineMetricsOf(state, [event(1, 10)], 10_000)).toEqual({
      ...ZERO_HEADLINE_METRICS,
      ratePerMinute: 1,
    });
  });
});

describe("headlineKpis", () => {
  it("reports the four operator rows with signed deltas", () => {
    expect(
      headlineKpis(
        { agents: 4, claims: 2, risk: 3, ratePerMinute: 9 },
        { agents: 6, claims: 1, risk: 0, ratePerMinute: 12 },
      ),
    ).toEqual([
      { label: "agents online", value: 6, delta: 2 },
      { label: "claims held", value: 1, delta: -1 },
      { label: "obs / min", value: 12, delta: 3 },
      { label: "risk signals", value: 0, delta: -3 },
    ]);
  });
});

describe("cockpitStamp", () => {
  it("distinguishes an unavailable timestamp from a browser-local clock value", () => {
    expect(cockpitStamp(null)).toBe("—");
    expect(cockpitStamp(Date.UTC(2026, 6, 23, 12, 34, 56))).toMatch(/^\d{2}:\d{2}:\d{2}$/u);
  });
});
