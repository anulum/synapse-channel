// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — headline cockpit metrics and visible freshness stamp

import type { Kpi } from "../components/Hud";
import type { SnapshotState } from "./snapshot";
import type { CockpitEvent } from "../types";

/** Values retained between renders so each KPI can report an exact delta. */
export interface HeadlineMetrics {
  readonly agents: number;
  readonly claims: number;
  readonly risk: number;
  readonly ratePerMinute: number;
}

/** Empty baseline used whenever a credential generation starts. */
export const ZERO_HEADLINE_METRICS: HeadlineMetrics = {
  agents: 0,
  claims: 0,
  risk: 0,
  ratePerMinute: 0,
};

/** Project the current snapshot and retained event minute into headline values. */
export function headlineMetricsOf(
  state: SnapshotState,
  log: readonly CockpitEvent[],
  nowMs: number,
): HeadlineMetrics {
  const since = nowMs / 1000 - 60;
  let ratePerMinute = 0;
  for (const event of log) {
    if (event.ts < since) break;
    ratePerMinute += 1;
  }
  const snapshot = state.snapshot;
  if (snapshot === null) return { ...ZERO_HEADLINE_METRICS, ratePerMinute };
  return {
    agents: snapshot.fleet.agents.live.length,
    claims: snapshot.fleet.claims.active,
    risk: snapshot.risk.signals.filter((signal) => signal.level === "red").length,
    ratePerMinute,
  };
}

/** Build the HUD rows and deltas from consecutive headline projections. */
export function headlineKpis(
  previous: HeadlineMetrics,
  current: HeadlineMetrics,
): readonly Kpi[] {
  return [
    { label: "agents online", value: current.agents, delta: current.agents - previous.agents },
    { label: "claims held", value: current.claims, delta: current.claims - previous.claims },
    {
      label: "obs / min",
      value: current.ratePerMinute,
      delta: current.ratePerMinute - previous.ratePerMinute,
    },
    { label: "risk signals", value: current.risk, delta: current.risk - previous.risk },
  ];
}

/** Format one browser-local freshness timestamp without implying a time zone. */
export function cockpitStamp(ms: number | null): string {
  if (ms === null) return "—";
  return new Date(ms).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}
