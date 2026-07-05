// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the sessions feed: what the fleet's work costs, per session

// `/sessions.json` aggregates the durable log's session_metric events —
// tokens, turns, errors, and cost per (agent, session), with a task_id
// where the bus could attribute one. That last field is the bridge the
// field does not have: cost joined to the SAME task ids the causality
// inspector traces, so "what did this task cost across every agent that
// touched it" is one filter, not instrumentation.

import { createEndpointFeed, type EndpointFeed, type FeedState } from "./feed";

/** One session's telemetry as the store reports it. */
export interface SessionRow {
  readonly agent: string;
  readonly sessionId: string;
  /** The task the bus attributed the session to; "" when unattributed. */
  readonly taskId: string;
  readonly turns: number;
  readonly errors: number;
  readonly abstentions: number;
  readonly inputTokens: number;
  readonly outputTokens: number;
  readonly totalTokens: number;
  /** Estimated cost in USD, or null when the metric carried none. */
  readonly costUsd: number | null;
  readonly seq: number;
  readonly ts: number;
}

/** The whole feed document. */
export interface SessionsReport {
  readonly generatedFromSeq: number;
  readonly asOf: number | null;
  readonly totals: Readonly<Record<string, number>>;
  readonly sessions: readonly SessionRow[];
  readonly note: string;
}

function asCount(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** Shape the sessions payload; null = not an object at all. */
export function parseSessions(raw: unknown): SessionsReport | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const payload = raw as Record<string, unknown>;
  const totalsRaw =
    typeof payload["totals"] === "object" && payload["totals"] !== null && !Array.isArray(payload["totals"])
      ? (payload["totals"] as Record<string, unknown>)
      : {};
  const totals: Record<string, number> = {};
  for (const [key, value] of Object.entries(totalsRaw)) if (typeof value === "number") totals[key] = value;
  const rows = Array.isArray(payload["sessions"]) ? payload["sessions"] : [];
  const sessions: SessionRow[] = [];
  for (const entry of rows) {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) continue;
    const record = entry as Record<string, unknown>;
    const cost = record["cost_usd"];
    sessions.push({
      agent: asString(record["agent"]),
      sessionId: asString(record["session_id"]),
      taskId: asString(record["task_id"]),
      turns: asCount(record["turns"]),
      errors: asCount(record["errors"]),
      abstentions: asCount(record["abstentions"]),
      inputTokens: asCount(record["input_tokens"]),
      outputTokens: asCount(record["output_tokens"]),
      totalTokens: asCount(record["total_tokens"]),
      costUsd: typeof cost === "number" && Number.isFinite(cost) ? cost : null,
      seq: asCount(record["seq"]),
      ts: asCount(record["ts"]),
    });
  }
  sessions.sort((a, b) => b.ts - a.ts || b.seq - a.seq);
  const asOf = payload["as_of"];
  return {
    generatedFromSeq: asCount(payload["generated_from_seq"]),
    asOf: typeof asOf === "number" && Number.isFinite(asOf) ? asOf : null,
    totals,
    sessions,
    note: asString(payload["note"]),
  };
}

/** The sessions feed's state; `absent` = the dashboard serves no endpoint. */
export type SessionsState = FeedState<SessionsReport>;

/** Poll the sessions feed with the shared endpoint lifecycle (30 s). */
export function createSessionsStore(options: {
  readonly url?: string;
  readonly pollMs?: number;
  readonly fetcher?: typeof fetch;
  readonly now?: () => number;
} = {}): EndpointFeed<SessionsReport> {
  return createEndpointFeed({
    url: options.url ?? "/sessions.json",
    pollMs: options.pollMs ?? 30_000,
    parse: parseSessions,
    ...(options.fetcher !== undefined ? { fetcher: options.fetcher } : {}),
    ...(options.now !== undefined ? { now: options.now } : {}),
  });
}
