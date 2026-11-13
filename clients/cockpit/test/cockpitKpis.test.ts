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
import { ENGLISH } from "../src/lib/i18n/catalogues/english";
import { FRENCH } from "../src/lib/i18n/catalogues/french";
import { GERMAN } from "../src/lib/i18n/catalogues/german";
import { SLOVAK } from "../src/lib/i18n/catalogues/slovak";
import { SPANISH } from "../src/lib/i18n/catalogues/spanish";
import { parseSnapshot, type SnapshotState } from "../src/lib/snapshot";
import type { CockpitEvent } from "../src/types";

function headline(counts: Record<string, number>): Record<string, number> {
  const keys = [
    "agents_live", "waiters_missing", "claims_active", "claims_stale", "tasks_open",
    "tasks_in_progress", "tasks_ready", "tasks_blocked", "tasks_blocked_marked",
    "tasks_blocked_waiting", "tasks_closed", "risk_signals", "risk_signals_red",
    "risk_signals_amber", "branch_conflicts", "peers_reachable", "peers_total",
    "seats_listening", "seats_active_not_listening", "seats_silent", "seats_deaf",
  ];
  return Object.fromEntries(keys.map((key) => [key, counts[key] ?? 0]));
}

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
  it("reads the hub's headline block and only the retained trailing minute", () => {
    const state: SnapshotState = {
      snapshot: parseSnapshot({
        // The lists disagree with the block on purpose: the HUD reads the block.
        headline: headline({ agents_live: 2, claims_active: 3, risk_signals: 3, risk_signals_red: 2 }),
        fleet: {
          agents: { live: ["alpha"] },
          claims: { active: 9 },
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

  it("shows red signals only and names that count", () => {
    const state: SnapshotState = {
      snapshot: parseSnapshot({
        headline: headline({ agents_live: 1, claims_active: 1, risk_signals: 1, risk_signals_amber: 1 }),
        fleet: { agents: { live: ["alpha"] }, claims: { active: 1 } },
        risk: {
          signals: [
            { level: "amber", category: "wait", subject: "T-2", detail: "gated" },
            { level: "green", category: "clear", subject: "T-4", detail: "quiet" },
          ],
        },
      }),
      status: "live",
      fetchedAt: 20_000,
      error: null,
    };

    expect(headlineMetricsOf(state, [], 20_000).risk).toBe(0);
    expect({
      en: ENGLISH["kpi.risk"],
      fr: FRENCH["kpi.risk"],
      de: GERMAN["kpi.risk"],
      sk: SLOVAK["kpi.risk"],
      es: SPANISH["kpi.risk"],
    }).toEqual({
      en: "red risk signals",
      fr: "signaux de risque rouges",
      de: "rote Risikosignale",
      sk: "červené rizikové signály",
      es: "señales de riesgo rojas",
    });
  });

  it("reads zero for the fleet's counters when the snapshot carries no headline", () => {
    const state: SnapshotState = {
      snapshot: parseSnapshot({ fleet: { agents: { live: ["alpha"] }, claims: { active: 4 } } }),
      status: "live",
      fetchedAt: 20_000,
      error: null,
    };

    expect(headlineMetricsOf(state, [event(1, 10)], 10_000)).toEqual({
      ...ZERO_HEADLINE_METRICS,
      ratePerMinute: 1,
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
