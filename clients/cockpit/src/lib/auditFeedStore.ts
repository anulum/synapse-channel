// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — retained cursor lifecycle for durable audit feeds

import { authenticatedFetch } from "./auth";
import {
  parseOperatorActionsPage,
  parseReceiptsPage,
  type AuditPage,
  type OperatorActionRow,
  type ReceiptRow,
} from "./auditFeedParser";
import type { EndpointFeed, FeedState } from "./feed";

/** Receipt polling state; an error may retain last-good rows. */
export type ReceiptsState = FeedState<readonly ReceiptRow[]>;

/** Operator-action polling state; an error may retain last-good rows. */
export type OperatorActionsState = FeedState<readonly OperatorActionRow[]>;

interface CursorFeedOptions<T extends { readonly seq: number }> {
  readonly url: string;
  readonly parse: (raw: unknown) => AuditPage<T> | null;
  readonly pollMs: number;
  readonly pageLimit: number;
  readonly retainedLimit: number;
  readonly fetcher?: typeof fetch;
  readonly now?: () => number;
}

/** Options shared by the two public audit-feed constructors. */
export interface AuditFeedOptions {
  readonly url?: string;
  readonly pollMs?: number;
  readonly pageLimit?: number;
  readonly retainedLimit?: number;
  readonly fetcher?: typeof fetch;
  readonly now?: () => number;
}

const DEFAULT_POLL_MS = 2_000;
const DEFAULT_PAGE_LIMIT = 50;
const DEFAULT_RETAINED_LIMIT = 100;

function mergeRows<T extends { readonly seq: number }>(
  current: readonly T[],
  incoming: readonly T[],
  retainedLimit: number,
): readonly T[] {
  const bySeq = new Map<number, T>();
  for (const row of current) bySeq.set(row.seq, row);
  for (const row of incoming) bySeq.set(row.seq, row);
  return [...bySeq.values()]
    .sort((left, right) => right.seq - left.seq)
    .slice(0, retainedLimit);
}

function createCursorFeed<T extends { readonly seq: number }>(
  options: CursorFeedOptions<T>,
): EndpointFeed<readonly T[]> {
  const fetcher = options.fetcher ?? authenticatedFetch;
  const now = options.now ?? Date.now;
  const pageLimit = Math.max(1, Math.floor(options.pageLimit));
  const retainedLimit = Math.max(1, Math.floor(options.retainedLimit));
  const listeners = new Set<(state: FeedState<readonly T[]>) => void>();
  let state: FeedState<readonly T[]> = {
    data: null,
    status: "connecting",
    fetchedAt: null,
    error: null,
  };
  let cursor = 0;
  let replaceOnNextLive = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  let stopped = false;

  const publish = (next: FeedState<readonly T[]>): void => {
    state = next;
    for (const listener of listeners) listener(state);
  };

  const poll = async (): Promise<void> => {
    controller = new AbortController();
    try {
      const response = await fetcher(`${options.url}?since=${cursor}&limit=${pageLimit}`, {
        signal: controller.signal,
      });
      if (response.status === 404) {
        cursor = 0;
        replaceOnNextLive = true;
        if (!stopped) {
          publish({ data: state.data, status: "absent", fetchedAt: state.fetchedAt, error: null });
        }
        return;
      }
      if (!response.ok) throw new Error(`hub returned ${response.status}`);
      const page = options.parse(await response.json());
      if (page === null) throw new Error("payload was not parseable");
      const maxRowSeq = page.rows.reduce((maximum, row) => Math.max(maximum, row.seq), cursor);
      cursor = Math.max(cursor, page.nextCursor, maxRowSeq);
      const base = replaceOnNextLive ? [] : (state.data ?? []);
      replaceOnNextLive = false;
      const data = mergeRows(base, page.rows, retainedLimit);
      if (!stopped) publish({ data, status: "live", fetchedAt: now(), error: null });
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

/** Poll the universal receipt ledger with a monotonic cursor and bounded history. */
export function createReceiptsStore(
  options: AuditFeedOptions = {},
): EndpointFeed<readonly ReceiptRow[]> {
  return createCursorFeed({
    url: options.url ?? "/receipts.json",
    parse: parseReceiptsPage,
    pollMs: options.pollMs ?? DEFAULT_POLL_MS,
    pageLimit: options.pageLimit ?? DEFAULT_PAGE_LIMIT,
    retainedLimit: options.retainedLimit ?? DEFAULT_RETAINED_LIMIT,
    ...(options.fetcher !== undefined ? { fetcher: options.fetcher } : {}),
    ...(options.now !== undefined ? { now: options.now } : {}),
  });
}

/** Poll governed operator relay history with a monotonic cursor and bounded history. */
export function createOperatorActionsStore(
  options: AuditFeedOptions = {},
): EndpointFeed<readonly OperatorActionRow[]> {
  return createCursorFeed({
    url: options.url ?? "/operator-actions.json",
    parse: parseOperatorActionsPage,
    pollMs: options.pollMs ?? DEFAULT_POLL_MS,
    pageLimit: options.pageLimit ?? DEFAULT_PAGE_LIMIT,
    retainedLimit: options.retainedLimit ?? DEFAULT_RETAINED_LIMIT,
    ...(options.fetcher !== undefined ? { fetcher: options.fetcher } : {}),
    ...(options.now !== undefined ? { now: options.now } : {}),
  });
}
