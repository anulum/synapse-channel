// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the health-anomalies feed: what the causality graph flags

// `/health-anomalies.json` is the hub's own anomaly report — orphaned claims
// (a claim is its task's final recorded word), dangling dependencies, and
// stale claims silent past the threshold — derived from the causal graph,
// not from thresholds the hub does not own. Each item points at concrete
// sequences, so a flag is always a pointer into the attested log, never a
// score.

import { createEndpointFeed, type EndpointFeed, type FeedState } from "./feed";

/** One flagged item; the fields present depend on the anomaly class. */
export interface AnomalyItem {
  readonly taskId: string;
  readonly owner: string;
  readonly detail: string;
  readonly seq: number | null;
}

/** The whole anomalies document. */
export interface HealthAnomalies {
  readonly present: boolean;
  readonly orphaned: readonly AnomalyItem[];
  readonly dangling: readonly AnomalyItem[];
  readonly stale: readonly AnomalyItem[];
  readonly anomalyCount: number;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asItems(value: unknown, detailOf: (record: Record<string, unknown>) => string): AnomalyItem[] {
  if (!Array.isArray(value)) return [];
  const items: AnomalyItem[] = [];
  for (const entry of value) {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) continue;
    const record = entry as Record<string, unknown>;
    const seq = record["seq"];
    items.push({
      taskId: asString(record["task_id"]),
      owner: asString(record["owner"]),
      detail: detailOf(record),
      seq: typeof seq === "number" && Number.isFinite(seq) ? seq : null,
    });
  }
  return items;
}

/** Shape the anomalies payload; null = not an object at all. */
export function parseHealthAnomalies(raw: unknown): HealthAnomalies | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const payload = raw as Record<string, unknown>;
  const orphaned = asItems(payload["orphaned"], (record) => {
    const age = record["age_seconds"];
    return typeof age === "number" && Number.isFinite(age)
      ? `claim is the task's last word · ${Math.round(age / 60)} min`
      : "claim is the task's last word";
  });
  const dangling = asItems(payload["dangling"], (record) => {
    // The server reports one absent prerequisite per row (a string); a list
    // is tolerated in case the shape ever widens.
    const raw = record["depends_on"];
    const deps =
      typeof raw === "string"
        ? [raw]
        : Array.isArray(raw)
          ? raw.filter((dep): dep is string => typeof dep === "string")
          : [];
    return deps.length > 0 ? `depends on absent ${deps.join(", ")}` : "depends on an absent task";
  });
  const stale = asItems(payload["stale"], (record) => {
    const age = record["age_seconds"];
    return typeof age === "number" && Number.isFinite(age)
      ? `unreleased and silent · ${Math.round(age / 60)} min`
      : "unreleased and silent";
  });
  const count = payload["anomaly_count"];
  return {
    present: payload["present"] !== false,
    orphaned,
    dangling,
    stale,
    anomalyCount:
      typeof count === "number" && Number.isFinite(count)
        ? count
        : orphaned.length + dangling.length + stale.length,
  };
}

/** The anomalies feed's state; `absent` = the dashboard serves no endpoint. */
export type HealthAnomaliesState = FeedState<HealthAnomalies>;

/** Poll the expensive whole-log anomalies feed with the shared lifecycle (2 min). */
export function createHealthAnomaliesStore(options: {
  readonly url?: string;
  readonly pollMs?: number;
  readonly fetcher?: typeof fetch;
  readonly now?: () => number;
} = {}): EndpointFeed<HealthAnomalies> {
  return createEndpointFeed({
    url: options.url ?? "/health-anomalies.json",
    pollMs: options.pollMs ?? 120_000,
    parse: parseHealthAnomalies,
    ...(options.fetcher !== undefined ? { fetcher: options.fetcher } : {}),
    ...(options.now !== undefined ? { now: options.now } : {}),
  });
}
