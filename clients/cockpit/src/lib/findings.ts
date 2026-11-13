// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the findings the hub recorded, read from the durable log
//
// `/findings.json` reads the journalled `finding` events the hub kept for the
// agents' `finding` requests, newest first. The board's progress ledger never
// carries findings (its note kinds do not include one), so this is the panel's
// only source. Without `--feeds-db` the dashboard answers 404 and the panel
// says so instead of showing an empty list.

import { createEndpointFeed, type EndpointFeed, type FeedState } from "./feed";

/** One recorded finding, as the feed serves it. */
export interface FindingRow {
  readonly seq: number;
  /** Epoch seconds the hub recorded the finding. */
  readonly ts: number | null;
  readonly statement: string;
  readonly subkind: string;
  /** The identity the hub attested as the finding's author. */
  readonly actor: string;
  readonly project: string;
  /** The honesty axes as recorded; "" when the producer left one unset. */
  readonly evidenceKind: string;
  readonly claimStatus: string;
  /** `verified-at-source` only when the producer re-checked the reference this session. */
  readonly freshness: string;
  /** `active`, `superseded`, `retracted`, or another recorded value. */
  readonly lifecycle: string;
  readonly evidenceRef: string;
}

/** One page of the feed. */
export interface FindingsReport {
  readonly findings: readonly FindingRow[];
  /** Every finding the log holds below the page's cursor. */
  readonly total: number;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asNumberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Shape the findings payload; null = not an object at all. */
export function parseFindings(raw: unknown): FindingsReport | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const payload = raw as Record<string, unknown>;
  const rows = Array.isArray(payload["findings"]) ? payload["findings"] : [];
  const findings: FindingRow[] = [];
  for (const entry of rows) {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) continue;
    const record = entry as Record<string, unknown>;
    const provenance = asRecord(record["provenance"]);
    findings.push({
      seq: asNumberOrNull(record["seq"]) ?? 0,
      ts: asNumberOrNull(record["ts"]),
      statement: asString(record["statement"]),
      subkind: asString(record["subkind"]),
      actor: asString(provenance["actor"]),
      project: asString(provenance["project"]),
      evidenceKind: asString(record["evidence_kind"]),
      claimStatus: asString(record["claim_status"]),
      freshness: asString(record["freshness"]),
      lifecycle: asString(record["lifecycle"]),
      evidenceRef: asString(record["evidence_ref"]),
    });
  }
  const total = asNumberOrNull(payload["total"]);
  return { findings, total: total === null ? findings.length : total };
}

/** The findings feed's state; `absent` = the dashboard serves no event store. */
export type FindingsState = FeedState<FindingsReport>;

/** Poll the findings feed with the shared endpoint lifecycle (15 s). */
export function createFindingsStore(
  options: {
    readonly url?: string;
    readonly pollMs?: number;
    readonly fetcher?: typeof fetch;
    readonly now?: () => number;
  } = {},
): EndpointFeed<FindingsReport> {
  return createEndpointFeed({
    url: options.url ?? "/findings.json",
    pollMs: options.pollMs ?? 15_000,
    parse: parseFindings,
    ...(options.fetcher !== undefined ? { fetcher: options.fetcher } : {}),
    ...(options.now !== undefined ? { now: options.now } : {}),
  });
}
