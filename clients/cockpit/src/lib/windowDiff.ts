// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — compare two windows of the attested log, count for count

// The field diffs model outputs; nobody diffs coordination behaviour. Two
// windows of the same durable log are exactly comparable — same source, same
// attestation — so the diff is arithmetic over facts, not judgement: per-kind
// deltas, the actors that appeared or went quiet, and each window's observed
// rate. Windows are sequence-addressed slices; their spans come from their
// own first/last timestamps, so an empty or single-event window states a
// rate of nothing rather than dividing by an invented duration.

import type { CockpitEvent } from "../types";

/** One kind's count in each window and the delta between them. */
export interface KindDelta {
  readonly kind: string;
  readonly a: number;
  readonly b: number;
  readonly delta: number;
}

/** The comparison of two log windows. */
export interface WindowDiff {
  /** Every kind either window saw: largest absolute delta first. */
  readonly kinds: readonly KindDelta[];
  /** Actors present in B but not A — who appeared. */
  readonly appeared: readonly string[];
  /** Actors present in A but not B — who went quiet. */
  readonly wentQuiet: readonly string[];
  /** Events per minute over each window's own span; null under 2 events. */
  readonly rateA: number | null;
  readonly rateB: number | null;
  readonly totalA: number;
  readonly totalB: number;
}

function countByKind(events: readonly CockpitEvent[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const event of events) counts.set(event.kind, (counts.get(event.kind) ?? 0) + 1);
  return counts;
}

function namedActors(events: readonly CockpitEvent[]): Set<string> {
  const actors = new Set<string>();
  for (const event of events) if (event.actor !== "") actors.add(event.actor);
  return actors;
}

/** Events per minute over the window's own first→last span; honest null under 2 events. */
export function observedRate(events: readonly CockpitEvent[]): number | null {
  if (events.length < 2) return null;
  let first = Infinity;
  let last = -Infinity;
  for (const event of events) {
    if (event.ts < first) first = event.ts;
    if (event.ts > last) last = event.ts;
  }
  if (last <= first) return null;
  return (events.length / (last - first)) * 60;
}

/** Compare window A against window B (both slices of the same attested log). */
export function diffWindows(
  a: readonly CockpitEvent[],
  b: readonly CockpitEvent[],
): WindowDiff {
  const countsA = countByKind(a);
  const countsB = countByKind(b);
  const kinds = [...new Set([...countsA.keys(), ...countsB.keys()])]
    .map((kind) => {
      const inA = countsA.get(kind) ?? 0;
      const inB = countsB.get(kind) ?? 0;
      return { kind, a: inA, b: inB, delta: inB - inA };
    })
    .sort(
      (left, right) => Math.abs(right.delta) - Math.abs(left.delta) || left.kind.localeCompare(right.kind),
    );

  const actorsA = namedActors(a);
  const actorsB = namedActors(b);
  const appeared = [...actorsB].filter((actor) => !actorsA.has(actor)).sort((x, y) => x.localeCompare(y));
  const wentQuiet = [...actorsA].filter((actor) => !actorsB.has(actor)).sort((x, y) => x.localeCompare(y));

  return {
    kinds,
    appeared,
    wentQuiet,
    rateA: observedRate(a),
    rateB: observedRate(b),
    totalA: a.length,
    totalB: b.length,
  };
}
