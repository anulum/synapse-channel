// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the log-pulse metrics feed: store-attested counts, never the wall clock

// `/metrics.json` aggregates the durable log — totals, per-kind counts, and
// trailing windows measured against the log's OWN final timestamp, so the
// document is deterministic over a given log and available with the hub down.
// Its `note` states the honest scope (the live process registry is the hub's
// own /metrics); the panel shows that note verbatim.

import { createEndpointFeed, type EndpointFeed, type FeedState } from "./feed";

/** Whole-log coverage facts. */
export interface LogCoverage {
  readonly totalEvents: number;
  readonly maxSeq: number;
  /** Epoch seconds of the first/last recorded event, or null on an empty log. */
  readonly firstTs: number | null;
  readonly lastTs: number | null;
}

/** One trailing window's counts (measured against the log's final ts). */
export interface MetricsWindow {
  readonly events: number;
  readonly byKind: Readonly<Record<string, number>>;
}

/** The log-pulse document as `/metrics.json` serves it. */
export interface LogMetrics {
  readonly source: string;
  readonly log: LogCoverage;
  readonly eventsByKind: Readonly<Record<string, number>>;
  /** Named trailing windows, e.g. `last_hour`, `last_day`. */
  readonly windows: Readonly<Record<string, MetricsWindow>>;
  /** The server's own scope statement, shown verbatim. */
  readonly note: string;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asCount(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? Math.trunc(value) : 0;
}

function asEpochOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asCountMap(value: unknown): Record<string, number> {
  const record = asRecord(value);
  const counts: Record<string, number> = {};
  for (const [kind, count] of Object.entries(record)) counts[kind] = asCount(count);
  return counts;
}

/**
 * Shape an untrusted metrics payload into {@link LogMetrics}. Returns `null`
 * only when the payload is not an object at all.
 */
export function parseMetrics(raw: unknown): LogMetrics | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const payload = asRecord(raw);
  const log = asRecord(payload["log"]);
  const windowsRaw = asRecord(payload["windows"]);
  const windows: Record<string, MetricsWindow> = {};
  for (const [name, window] of Object.entries(windowsRaw)) {
    const record = asRecord(window);
    windows[name] = { events: asCount(record["events"]), byKind: asCountMap(record["by_kind"]) };
  }
  return {
    source: typeof payload["source"] === "string" ? payload["source"] : "",
    log: {
      totalEvents: asCount(log["total_events"]),
      maxSeq: asCount(log["max_seq"]),
      firstTs: asEpochOrNull(log["first_ts"]),
      lastTs: asEpochOrNull(log["last_ts"]),
    },
    eventsByKind: asCountMap(payload["events_by_kind"]),
    windows,
    note: typeof payload["note"] === "string" ? payload["note"] : "",
  };
}

/**
 * Order a kind-count map for display: largest count first, ties by kind name.
 * Returns entries, so the renderer never re-sorts.
 */
export function orderKindCounts(
  counts: Readonly<Record<string, number>>,
): [string, number][] {
  return Object.entries(counts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

/** The metrics feed's state; `absent` means the hub serves no endpoint. */
export type MetricsState = FeedState<LogMetrics>;

export interface MetricsStoreOptions {
  readonly url?: string;
  readonly pollMs?: number;
  readonly fetcher?: typeof fetch;
  readonly now?: () => number;
}

const DEFAULT_METRICS_URL = "/metrics.json";
const DEFAULT_METRICS_POLL_MS = 30_000;

/**
 * Poll the log-pulse metrics with the shared feed lifecycle: `404` reports
 * `absent` and re-checks, so the panel comes alive when the operator passes
 * `--feeds-db`.
 */
export function createMetricsStore(options: MetricsStoreOptions = {}): EndpointFeed<LogMetrics> {
  return createEndpointFeed({
    url: options.url ?? DEFAULT_METRICS_URL,
    pollMs: options.pollMs ?? DEFAULT_METRICS_POLL_MS,
    parse: parseMetrics,
    ...(options.fetcher !== undefined ? { fetcher: options.fetcher } : {}),
    ...(options.now !== undefined ? { now: options.now } : {}),
  });
}
