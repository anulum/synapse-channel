// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the history scrub: any window of the attested log, on demand

// The field's time-travel replays re-execute; the read-only analogue is a
// scrubber over the durable log. The hub's sequence is the natural axis —
// dense, monotonic, attested — so the scrubber picks a sequence and this
// module fetches the window that ends there. History exists only where the
// hub's log does: the scrub is offered on hub provenance and nowhere else.

import { mapStoredEvent, parseTail } from "./eventsTail";
import type { CockpitEvent } from "../types";
import { authenticatedFetch } from "./auth";

/** One fetched slice of the log's history. */
export interface HistoryWindow {
  /** The window's events, newest first (the log's order). */
  readonly events: readonly CockpitEvent[];
  /** First (oldest) sequence in the window, or 0 for an empty one. */
  readonly fromSeq: number;
  /** Last (newest) sequence in the window — the scrub position. */
  readonly toSeq: number;
}

/** A history fetch outcome; `absent` = the dashboard serves no event feed. */
export type HistoryResult =
  | { readonly kind: "loaded"; readonly window: HistoryWindow }
  | { readonly kind: "absent" }
  | { readonly kind: "error"; readonly message: string };

const EVENTS_URL = "/events.json";

/** How many events one scrub position shows. */
export const HISTORY_WINDOW_SIZE = 200;

/**
 * Fetch the newest sequence in the log — the scrubber's right edge. Uses the
 * `since=latest` shortcut, so the cost is one request on a log of any size.
 */
export async function fetchLatestSeq(
  fetcher: typeof fetch = authenticatedFetch,
  url: string = EVENTS_URL,
): Promise<{ kind: "loaded"; latest: number } | { kind: "absent" } | { kind: "error"; message: string }> {
  try {
    const response = await fetcher(`${url}?since=latest&limit=1`);
    if (response.status === 404) return { kind: "absent" };
    if (!response.ok) return { kind: "error", message: `hub returned ${response.status}` };
    const tail = parseTail(await response.json());
    if (tail === null) return { kind: "error", message: "events payload was not an object" };
    return { kind: "loaded", latest: tail.nextCursor };
  } catch (cause) {
    return { kind: "error", message: cause instanceof Error ? cause.message : String(cause) };
  }
}

/**
 * Fetch the history window ending at `toSeq` (inclusive): the events with
 * sequences in `(toSeq - limit, toSeq]`, returned newest first. A position
 * before the log's start yields whatever exists.
 */
export async function fetchHistoryWindow(
  toSeq: number,
  limit: number = HISTORY_WINDOW_SIZE,
  fetcher: typeof fetch = authenticatedFetch,
  url: string = EVENTS_URL,
): Promise<HistoryResult> {
  const since = Math.max(0, Math.trunc(toSeq) - limit);
  try {
    const response = await fetcher(`${url}?since=${since}&limit=${limit}`);
    if (response.status === 404) return { kind: "absent" };
    if (!response.ok) return { kind: "error", message: `hub returned ${response.status}` };
    const tail = parseTail(await response.json());
    if (tail === null) return { kind: "error", message: "events payload was not an object" };
    const kept = tail.events.filter((event) => event.seq <= toSeq);
    const events = kept.map(mapStoredEvent).reverse();
    const oldest = kept[0];
    return {
      kind: "loaded",
      window: {
        events,
        fromSeq: oldest === undefined ? 0 : oldest.seq,
        toSeq: Math.trunc(toSeq),
      },
    };
  } catch (cause) {
    return { kind: "error", message: cause instanceof Error ? cause.message : String(cause) };
  }
}
