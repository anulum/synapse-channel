// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — retained event-window coverage model

import type { CockpitEvent } from "../types";

export const EVENT_RETENTION_LIMIT = 250;

export type EventCoverageSource = "connecting" | "hub" | "derived";

export interface EventCoverage {
  readonly source: EventCoverageSource;
  readonly retained: number;
  readonly capacity: number;
  readonly minSeq: number | null;
  readonly maxSeq: number | null;
  readonly minTs: number | null;
  readonly maxTs: number | null;
  readonly atCapacity: boolean;
}

export function eventCoverageOf(
  events: readonly CockpitEvent[],
  source: EventCoverageSource,
): EventCoverage {
  let minSeq: number | null = null;
  let maxSeq: number | null = null;
  let minTs: number | null = null;
  let maxTs: number | null = null;

  for (const event of events) {
    minSeq = minSeq === null ? event.seq : Math.min(minSeq, event.seq);
    maxSeq = maxSeq === null ? event.seq : Math.max(maxSeq, event.seq);
    minTs = minTs === null ? event.ts : Math.min(minTs, event.ts);
    maxTs = maxTs === null ? event.ts : Math.max(maxTs, event.ts);
  }

  return {
    source,
    retained: events.length,
    capacity: EVENT_RETENTION_LIMIT,
    minSeq,
    maxSeq,
    minTs,
    maxTs,
    atCapacity: events.length >= EVENT_RETENTION_LIMIT,
  };
}
