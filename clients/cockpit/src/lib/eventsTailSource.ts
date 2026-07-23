// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — authenticated cursor lifecycle for hub-attested events

import type { CockpitEvent } from "../types";
import { authenticatedFetch } from "./auth";
import { mapStoredEvent } from "./eventProjection";
import { parseTail, type ParsedEventsTail, type StoredEvent } from "./eventTailParser";

/** Where the spine's events come from right now. */
export type SpineProvenance = "connecting" | "hub" | "absent" | "error";

/** A feed of hub-attested events plus its provenance state. */
export interface EventsTailSource {
  /** Register an event listener; returns an unsubscribe handle. */
  subscribe(listener: (event: CockpitEvent) => void): () => void;
  /** Register a provenance listener (called with the current state at once). */
  subscribeMode(listener: (mode: SpineProvenance) => void): () => void;
  /** Stop polling, abort the active request, and release every listener. */
  stop(): void;
}

/** Configuration for one authenticated event-tail source. */
export interface EventsTailOptions {
  /** Endpoint to poll; defaults to the dashboard-served `/events.json`. */
  readonly url?: string;
  /** Incremental poll cadence in milliseconds. */
  readonly pollMs?: number;
  /** Re-check cadence while the endpoint is absent. */
  readonly absentPollMs?: number;
  /** Page size for each incremental poll. */
  readonly limit?: number;
  /** How many tail events the first-contact backfill emits as history. */
  readonly historyLimit?: number;
  /** Injectable fetch for tests; defaults to the global. */
  readonly fetcher?: typeof fetch;
}

const DEFAULT_EVENTS_URL = "/events.json";
const DEFAULT_EVENTS_POLL_MS = 2_000;
const DEFAULT_ABSENT_POLL_MS = 10_000;
const DEFAULT_PAGE_LIMIT = 1_000;
const DEFAULT_HISTORY_LIMIT = 250;

type FetchedPage = ParsedEventsTail | "absent";

/**
 * Poll the hub-attested event tail. A current server answers the first
 * `since=latest&history=1` request with the cursor and recent evidence in one
 * response. An older server ignores that opt-in marker, so the client falls
 * back to the original second backfill request. Incremental polling then
 * proceeds from the cursor. A `404` reports `absent` and re-checks slowly, so
 * the feed comes alive the moment the operator passes `--feeds-db`; any other
 * failure reports `error` and keeps trying on the normal cadence.
 */
export function createEventsTailSource(options: EventsTailOptions = {}): EventsTailSource {
  const url = options.url ?? DEFAULT_EVENTS_URL;
  const pollMs = options.pollMs ?? DEFAULT_EVENTS_POLL_MS;
  const absentPollMs = options.absentPollMs ?? DEFAULT_ABSENT_POLL_MS;
  const limit = options.limit ?? DEFAULT_PAGE_LIMIT;
  const historyLimit = options.historyLimit ?? DEFAULT_HISTORY_LIMIT;
  const fetcher = options.fetcher ?? authenticatedFetch;

  const listeners = new Set<(event: CockpitEvent) => void>();
  const modeListeners = new Set<(mode: SpineProvenance) => void>();
  let mode: SpineProvenance = "connecting";
  let cursor = 0;
  let caughtUp = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  let stopped = false;

  const setMode = (next: SpineProvenance): void => {
    if (mode === next) return;
    mode = next;
    for (const listener of modeListeners) listener(mode);
  };

  const emit = (events: readonly StoredEvent[]): void => {
    for (const stored of events) {
      const event = mapStoredEvent(stored);
      for (const listener of listeners) listener(event);
    }
  };

  const fetchPage = async (
    since: number | "latest",
    pageLimit: number,
    includeHistory = false,
  ): Promise<FetchedPage> => {
    controller = new AbortController();
    const historyQuery = includeHistory ? "&history=1" : "";
    const response = await fetcher(`${url}?since=${since}&limit=${pageLimit}${historyQuery}`, {
      signal: controller.signal,
    });
    if (response.status === 404) return "absent";
    if (!response.ok) throw new Error(`hub returned ${response.status}`);
    const tail = parseTail(await response.json());
    if (tail === null) throw new Error("events payload was not an object");
    return tail;
  };

  const poll = async (): Promise<void> => {
    let delay = pollMs;
    try {
      if (!caughtUp) {
        const tip = await fetchPage("latest", historyLimit, true);
        if (tip === "absent") {
          setMode("absent");
          delay = absentPollMs;
          return;
        }
        if (stopped) return;
        if (tip.historyIncluded) {
          cursor = tip.nextCursor;
          caughtUp = true;
          setMode("hub");
          if (!stopped) emit(tip.events);
          return;
        }
        const backfillFrom = Math.max(0, tip.nextCursor - historyLimit);
        const history = await fetchPage(backfillFrom, historyLimit);
        if (history === "absent") {
          setMode("absent");
          delay = absentPollMs;
          return;
        }
        cursor = Math.max(tip.nextCursor, history.nextCursor);
        caughtUp = true;
        setMode("hub");
        if (!stopped) emit(history.events);
        return;
      }
      const page = await fetchPage(cursor, limit);
      if (page === "absent") {
        caughtUp = false;
        setMode("absent");
        delay = absentPollMs;
        return;
      }
      cursor = page.nextCursor;
      setMode("hub");
      if (!stopped) emit(page.events);
    } catch (cause) {
      if (stopped) return;
      setMode(caughtUp ? "error" : mode === "absent" ? "absent" : "error");
      void cause;
    } finally {
      if (!stopped) timer = setTimeout(poll, delay);
    }
  };

  void poll();

  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    subscribeMode(listener) {
      modeListeners.add(listener);
      listener(mode);
      return () => modeListeners.delete(listener);
    },
    stop() {
      stopped = true;
      if (timer !== undefined) clearTimeout(timer);
      controller?.abort();
      listeners.clear();
      modeListeners.clear();
    },
  };
}
