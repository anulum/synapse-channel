// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — authenticated snapshot polling and freshness lifecycle

import type { FleetSnapshot } from "../types";
import { authenticatedFetch } from "./auth";
import { parseSnapshot } from "./snapshotParser";

/** Connection state of the snapshot feed, for the freshness contract. */
export type SnapshotStatus = "connecting" | "live" | "stale" | "error";

/** The latest fleet snapshot plus how fresh and trustworthy it is. */
export interface SnapshotState {
  readonly snapshot: FleetSnapshot | null;
  readonly status: SnapshotStatus;
  /** Epoch milliseconds of the last successful fetch, or null before one. */
  readonly fetchedAt: number | null;
  /** Human-readable reason for the last failure, or null when healthy. */
  readonly error: string | null;
}

/** A polling feed of the fleet snapshot. */
export interface SnapshotStore {
  subscribe(listener: (state: SnapshotState) => void): () => void;
  stop(): void;
}

/** Injectable endpoint, cadence, transport, and clock for a snapshot store. */
export interface SnapshotStoreOptions {
  /** Endpoint to poll; defaults to the dev-proxied `/snapshot.json`. */
  readonly url?: string;
  /** Poll cadence in milliseconds. */
  readonly pollMs?: number;
  /** Age past which the newest snapshot is reported `stale`. */
  readonly staleAfterMs?: number;
  /** Injectable fetch for tests; defaults to the authenticated fetch. */
  readonly fetcher?: typeof fetch;
  /** Injectable clock for tests; defaults to `Date.now`. */
  readonly now?: () => number;
}

const DEFAULT_URL = "/snapshot.json";
const DEFAULT_POLL_MS = 2000;
const DEFAULT_STALE_AFTER_MS = 6000;

function isStale(fetchedAt: number, now: number, staleAfterMs: number): boolean {
  return now - fetchedAt > staleAfterMs;
}

/**
 * Create a snapshot store that polls the hub on a fixed cadence. Listeners
 * receive a state after each poll; transient errors retain the last good
 * document while freshness controls its visible status.
 */
export function createSnapshotStore(options: SnapshotStoreOptions = {}): SnapshotStore {
  const url = options.url ?? DEFAULT_URL;
  const pollMs = options.pollMs ?? DEFAULT_POLL_MS;
  const staleAfterMs = options.staleAfterMs ?? DEFAULT_STALE_AFTER_MS;
  const fetcher = options.fetcher ?? authenticatedFetch;
  const now = options.now ?? Date.now;

  const listeners = new Set<(state: SnapshotState) => void>();
  let state: SnapshotState = {
    snapshot: null,
    status: "connecting",
    fetchedAt: null,
    error: null,
  };
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  let stopped = false;

  const publish = (next: SnapshotState): void => {
    state = next;
    for (const listener of listeners) listener(state);
  };

  const poll = async (): Promise<void> => {
    controller = new AbortController();
    try {
      const response = await fetcher(url, { signal: controller.signal });
      if (!response.ok) throw new Error(`hub returned ${response.status}`);
      const snapshot = parseSnapshot(await response.json());
      if (snapshot === null) throw new Error("snapshot payload was not an object");
      if (!stopped) {
        publish({ snapshot, status: "live", fetchedAt: now(), error: null });
      }
    } catch (cause) {
      if (stopped) return;
      const message = cause instanceof Error ? cause.message : String(cause);
      const held = state.fetchedAt;
      const status: SnapshotStatus =
        state.snapshot === null || held === null
          ? "error"
          : isStale(held, now(), staleAfterMs)
            ? "stale"
            : "live";
      publish({ snapshot: state.snapshot, status, fetchedAt: state.fetchedAt, error: message });
    } finally {
      if (!stopped) timer = setTimeout(poll, pollMs);
    }
  };

  void poll();

  return {
    subscribe(listener) {
      listeners.add(listener);
      listener(state);
      return () => listeners.delete(listener);
    },
    stop() {
      stopped = true;
      if (timer !== undefined) clearTimeout(timer);
      controller?.abort();
      listeners.clear();
    },
  };
}

/** Re-evaluate a state's freshness against a clock, without a new fetch. */
export function withFreshness(
  state: SnapshotState,
  now: number,
  staleAfterMs = DEFAULT_STALE_AFTER_MS,
): SnapshotState {
  if (state.fetchedAt === null || state.snapshot === null) return state;
  const status: SnapshotStatus = isStale(state.fetchedAt, now, staleAfterMs) ? "stale" : "live";
  return status === state.status ? state : { ...state, status };
}
