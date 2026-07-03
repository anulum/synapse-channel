// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — a polling JSON endpoint feed with explicit absence semantics

// Optional dashboard surfaces (reliability evidence, federation posture) share
// one lifecycle: poll slowly, treat HTTP 404 as "this hub's dashboard does not
// serve the surface" — a state the panel must say out loud, distinct from a
// failure — keep the last good payload across transient errors, and re-check
// on the same cadence so the panel comes alive the moment the server ships.

/** Connection state of an endpoint feed. `absent` = the hub has no endpoint. */
export type FeedStatus = "connecting" | "live" | "absent" | "error";

/** The latest parsed payload plus how it was (or was not) obtained. */
export interface FeedState<T> {
  readonly data: T | null;
  readonly status: FeedStatus;
  /** Epoch milliseconds of the last successful fetch, or null before one. */
  readonly fetchedAt: number | null;
  /** Human-readable reason for the last failure, or null when healthy. */
  readonly error: string | null;
}

/** A polling feed of one optional dashboard endpoint. */
export interface EndpointFeed<T> {
  subscribe(listener: (state: FeedState<T>) => void): () => void;
  stop(): void;
}

export interface EndpointFeedOptions<T> {
  /** Endpoint to poll. */
  readonly url: string;
  /** Poll cadence in milliseconds. */
  readonly pollMs: number;
  /** Payload parser; returning `null` marks the poll as a payload error. */
  readonly parse: (raw: unknown) => T | null;
  /** Injectable fetch for tests; defaults to the global. */
  readonly fetcher?: typeof fetch;
  /** Injectable clock for tests; defaults to `Date.now`. */
  readonly now?: () => number;
}

/**
 * Poll `url` on a fixed cadence and publish {@link FeedState} transitions:
 * `404` → `absent` (re-checked every poll), a valid payload → `live`, any
 * other failure → `error` with the reason while the last good payload is
 * retained. Listeners receive the current state on subscribe.
 */
export function createEndpointFeed<T>(options: EndpointFeedOptions<T>): EndpointFeed<T> {
  const fetcher = options.fetcher ?? fetch;
  const now = options.now ?? Date.now;

  const listeners = new Set<(state: FeedState<T>) => void>();
  let state: FeedState<T> = { data: null, status: "connecting", fetchedAt: null, error: null };
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  let stopped = false;

  const publish = (next: FeedState<T>): void => {
    state = next;
    for (const listener of listeners) listener(state);
  };

  const poll = async (): Promise<void> => {
    controller = new AbortController();
    try {
      const response = await fetcher(options.url, { signal: controller.signal });
      if (response.status === 404) {
        if (!stopped) {
          publish({ data: state.data, status: "absent", fetchedAt: state.fetchedAt, error: null });
        }
        return;
      }
      if (!response.ok) throw new Error(`hub returned ${response.status}`);
      const data = options.parse(await response.json());
      if (data === null) throw new Error("payload was not parseable");
      if (!stopped) {
        publish({ data, status: "live", fetchedAt: now(), error: null });
      }
    } catch (cause) {
      if (stopped) return;
      const message = cause instanceof Error ? cause.message : String(cause);
      publish({ data: state.data, status: "error", fetchedAt: state.fetchedAt, error: message });
    } finally {
      if (!stopped) timer = setTimeout(poll, options.pollMs);
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
