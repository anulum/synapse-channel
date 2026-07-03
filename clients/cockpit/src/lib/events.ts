// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the cockpit event model: lanes and semantic colours

import type { EventKind, Lane } from "../types";

/** The lane each event kind rides. */
const LANE_OF: Record<EventKind, Lane> = {
  presence: "presence",
  claim: "claims",
  lease: "claims",
  release: "claims",
  task: "task",
  chat: "presence",
  finding: "task",
  conflict: "risk",
};

/** The fixed semantic colour token each event kind carries, everywhere. */
export const COLOUR_OF: Record<EventKind, string> = {
  presence: "var(--info)",
  claim: "var(--info)",
  lease: "var(--warn)",
  release: "var(--healthy)",
  task: "var(--healthy)",
  chat: "var(--dim)",
  finding: "var(--info)",
  conflict: "var(--critical)",
};

/**
 * Severity per event kind, 0..1 — drives impulse height on the spine. One
 * policy for every event source, derived or hub-attested.
 */
export const SEVERITY_OF: Record<EventKind, number> = {
  presence: 0.3,
  claim: 0.45,
  lease: 0.7,
  release: 0.5,
  task: 0.6,
  chat: 0.25,
  finding: 0.55,
  conflict: 0.9,
};

/** The lane an operator scans peripherally; a deflection here is an alarm. */
export const RISK_LANE: Lane = "risk";

/** The lanes in vertical render order (top to bottom). */
export const LANES: readonly Lane[] = ["presence", "claims", "task", "risk"];

/** Return the lane an event kind belongs to. */
export function laneOf(kind: EventKind): Lane {
  return LANE_OF[kind];
}
