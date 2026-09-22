// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — owner-local attention feed contract

import { createEndpointFeed, type FeedState } from "./feed";

export interface StoredAttentionAlert {
  readonly key: string;
  readonly kind: "approval" | "failed_delivery" | "recovery" | "stale_data" | "quota_reset";
  readonly subject: string;
  readonly severity: "critical" | "warning" | "info";
  readonly state: "open" | "expired";
  readonly action: string;
  readonly observedAt: number;
  readonly expiresAt: number | null;
}

export interface AttentionReport {
  readonly state: "active" | "quiet" | "missing_observer";
  readonly alerts: readonly StoredAttentionAlert[];
  readonly remaining: number;
  readonly snoozedCount: number;
}

export type AttentionFeedState = FeedState<AttentionReport>;

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

/** Parse only the bounded fields the authenticated attention panel may render. */
export function parseAttentionReport(raw: unknown): AttentionReport | null {
  const source = record(raw);
  if (source === null || source["version"] !== 1 || !Array.isArray(source["alerts"])) return null;
  const state = source["state"];
  if (state !== "active" && state !== "quiet" && state !== "missing_observer") return null;
  const remaining = source["remaining"];
  const snoozed = source["snoozed_count"];
  if (!Number.isSafeInteger(remaining) || !Number.isSafeInteger(snoozed) ||
      (remaining as number) < 0 || (snoozed as number) < 0 || source["alerts"].length > 200) return null;
  const alerts: StoredAttentionAlert[] = [];
  for (const value of source["alerts"]) {
    const item = record(value);
    if (item === null) return null;
    const { key, kind, subject, severity, state: alertState, action } = item;
    const observedAt = item["observed_at"];
    const expiresAt = item["expires_at"];
    if (typeof key !== "string" || key.length === 0 || key.length > 256 ||
        (kind !== "approval" && kind !== "failed_delivery" && kind !== "recovery" &&
         kind !== "stale_data" && kind !== "quota_reset") ||
        typeof subject !== "string" || subject.length > 256 ||
        typeof action !== "string" || action.length > 256 ||
        (severity !== "critical" && severity !== "warning" && severity !== "info") ||
        (alertState !== "open" && alertState !== "expired") ||
        typeof observedAt !== "number" || !Number.isFinite(observedAt) ||
        (expiresAt !== null && (typeof expiresAt !== "number" || !Number.isFinite(expiresAt)))) return null;
    alerts.push({ key, kind, subject, severity, state: alertState, action, observedAt, expiresAt });
  }
  return { state, alerts, remaining: remaining as number, snoozedCount: snoozed as number };
}

/** Poll the optional authenticated queue without conflating 404 and quiet. */
export function createAttentionStore(options: {
  readonly url?: string;
  readonly pollMs?: number;
  readonly fetcher?: typeof fetch;
} = {}) {
  return createEndpointFeed({
    url: options.url ?? "/attention.json",
    pollMs: options.pollMs ?? 5_000,
    parse: parseAttentionReport,
    ...(options.fetcher === undefined ? {} : { fetcher: options.fetcher }),
  });
}
