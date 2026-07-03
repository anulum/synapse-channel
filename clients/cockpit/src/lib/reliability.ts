// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the reliability EVIDENCE feed: recorded audit signals, never scores

// The core reliability module is deliberately not a score, and this panel keeps
// that boundary: it renders the hub's recorded findings and per-owner tallies of
// those findings verbatim (the hub's own `note` says "audit signals, not
// scores") and never folds them into a rank, grade, or number of merit. The
// operator draws the conclusion; the panel shows the record.

import { createEndpointFeed, type EndpointFeed, type FeedState } from "./feed";

/** One recorded reliability finding, pointing back at its event seq. */
export interface ReliabilityFinding {
  readonly kind: string;
  readonly owner: string;
  readonly taskId: string;
  /** Durable event-log sequence the finding is anchored to. */
  readonly seq: number;
  /** Epoch seconds of the anchoring event, or null when absent. */
  readonly ts: number | null;
  /** The hub's own one-line description of the evidence. */
  readonly detail: string;
  /** The structured evidence record, kind-specific, rendered on demand. */
  readonly evidence: Record<string, unknown>;
}

/** Per-owner tallies of recorded findings — counts of events, not a score. */
export interface OwnerEvidence {
  readonly owner: string;
  readonly staleClaims: number;
  readonly conflictPairs: number;
  readonly declaredFailedChecks: number;
  readonly brokenHandoffs: number;
}

/** The reliability report as `synapse reliability --json` shapes it. */
export interface ReliabilityReport {
  /** Timestamp the stale-lease checks were evaluated against. */
  readonly asOf: number | null;
  /** Last event seq the report was generated from. */
  readonly generatedFromSeq: number | null;
  /** The hub's own boundary statement, e.g. "audit signals, not scores". */
  readonly note: string;
  readonly owners: readonly OwnerEvidence[];
  readonly findings: readonly ReliabilityFinding[];
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asCount(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? Math.trunc(value) : 0;
}

function asEpochOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function parseFinding(value: unknown): ReliabilityFinding {
  const finding = asRecord(value);
  return {
    kind: asString(finding["kind"]),
    owner: asString(finding["owner"]),
    taskId: asString(finding["task_id"]),
    seq: asCount(finding["seq"]),
    ts: asEpochOrNull(finding["ts"]),
    detail: asString(finding["detail"]),
    evidence: asRecord(finding["evidence"]),
  };
}

function parseOwner(value: unknown): OwnerEvidence {
  const owner = asRecord(value);
  return {
    owner: asString(owner["owner"]),
    staleClaims: asCount(owner["stale_claims"]),
    conflictPairs: asCount(owner["conflict_pairs"]),
    declaredFailedChecks: asCount(owner["declared_failed_checks"]),
    brokenHandoffs: asCount(owner["broken_handoffs"]),
  };
}

/**
 * Shape an untrusted reliability payload into a {@link ReliabilityReport}.
 * Returns `null` only when the payload is not an object at all; any object,
 * however partial, yields a report with safe empty defaults.
 */
export function parseReliability(raw: unknown): ReliabilityReport | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const payload = asRecord(raw);
  return {
    asOf: asEpochOrNull(payload["as_of"]),
    generatedFromSeq: asEpochOrNull(payload["generated_from_seq"]),
    note: asString(payload["note"]),
    owners: Array.isArray(payload["owners"]) ? payload["owners"].map(parseOwner) : [],
    findings: Array.isArray(payload["findings"]) ? payload["findings"].map(parseFinding) : [],
  };
}

/**
 * Order owners by recorded-evidence volume (most recorded findings first, then
 * by name) so the tally table surfaces the most-documented agents without ever
 * ranking by merit — volume of records is not a verdict, and the panel says so.
 */
export function orderOwners(owners: readonly OwnerEvidence[]): OwnerEvidence[] {
  const volume = (owner: OwnerEvidence): number =>
    owner.staleClaims + owner.conflictPairs + owner.declaredFailedChecks + owner.brokenHandoffs;
  return [...owners].sort((a, b) => volume(b) - volume(a) || a.owner.localeCompare(b.owner));
}

/** The reliability feed's state; `absent` means the hub serves no endpoint. */
export type ReliabilityState = FeedState<ReliabilityReport>;

export interface ReliabilityStoreOptions {
  /** Endpoint to poll; defaults to the dashboard-served `/reliability.json`. */
  readonly url?: string;
  /** Poll cadence in milliseconds; the report is log-derived, so poll slowly. */
  readonly pollMs?: number;
  /** Injectable fetch for tests; defaults to the global. */
  readonly fetcher?: typeof fetch;
  /** Injectable clock for tests; defaults to `Date.now`. */
  readonly now?: () => number;
}

const DEFAULT_RELIABILITY_URL = "/reliability.json";
const DEFAULT_RELIABILITY_POLL_MS = 15_000;

/**
 * Poll the hub's reliability endpoint with the shared feed lifecycle: `404`
 * reports `absent` and keeps re-checking, so the panel comes alive the moment
 * the server side ships; other failures keep the last good report.
 */
export function createReliabilityStore(
  options: ReliabilityStoreOptions = {},
): EndpointFeed<ReliabilityReport> {
  return createEndpointFeed({
    url: options.url ?? DEFAULT_RELIABILITY_URL,
    pollMs: options.pollMs ?? DEFAULT_RELIABILITY_POLL_MS,
    parse: parseReliability,
    ...(options.fetcher !== undefined ? { fetcher: options.fetcher } : {}),
    ...(options.now !== undefined ? { now: options.now } : {}),
  });
}
