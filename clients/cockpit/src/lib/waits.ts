// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the waits feed: who is gated on what, read-only

// `/waits.json` names the fleet's pending coordination gates — the tasks
// standing behind unmet dependencies, who suggested-owns them, and since
// when. Read-only visibility into the human/agent decision queue; acting
// on a gate stays wherever it always was (the bus, the CLI, an armed
// operator dashboard).

import { createEndpointFeed, type EndpointFeed, type FeedState } from "./feed";

/** One pending gate. */
export interface WaitRow {
  readonly taskId: string;
  readonly title: string;
  readonly who: string;
  readonly onWhat: readonly string[];
  /** Epoch seconds the wait began, or null when unrecorded. */
  readonly since: number | null;
  readonly status: string;
}

/** The whole waits document. */
export interface WaitsReport {
  readonly present: boolean;
  readonly waits: readonly WaitRow[];
  readonly waitCount: number;
  readonly logEndSeq: number;
  readonly note: string;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** Shape the waits payload; null = not an object at all. */
export function parseWaits(raw: unknown): WaitsReport | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const payload = raw as Record<string, unknown>;
  const rows = Array.isArray(payload["waits"]) ? payload["waits"] : [];
  const waits: WaitRow[] = [];
  for (const entry of rows) {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) continue;
    const record = entry as Record<string, unknown>;
    const since = record["since"];
    waits.push({
      taskId: asString(record["task_id"]),
      title: asString(record["title"]),
      who: asString(record["who"]),
      onWhat: Array.isArray(record["on_what"])
        ? record["on_what"].filter((dep): dep is string => typeof dep === "string")
        : [],
      since: typeof since === "number" && Number.isFinite(since) ? since : null,
      status: asString(record["status"]),
    });
  }
  return {
    present: payload["present"] !== false,
    waits,
    waitCount:
      typeof payload["wait_count"] === "number" && Number.isFinite(payload["wait_count"])
        ? payload["wait_count"]
        : waits.length,
    logEndSeq:
      typeof payload["log_end_seq"] === "number" && Number.isFinite(payload["log_end_seq"])
        ? payload["log_end_seq"]
        : 0,
    note: asString(payload["note"]),
  };
}

/** The waits feed's state; `absent` = the dashboard serves no endpoint. */
export type WaitsState = FeedState<WaitsReport>;

/** Poll the waits feed with the shared endpoint lifecycle (15 s). */
export function createWaitsStore(options: {
  readonly url?: string;
  readonly pollMs?: number;
  readonly fetcher?: typeof fetch;
  readonly now?: () => number;
} = {}): EndpointFeed<WaitsReport> {
  return createEndpointFeed({
    url: options.url ?? "/waits.json",
    pollMs: options.pollMs ?? 15_000,
    parse: parseWaits,
    ...(options.fetcher !== undefined ? { fetcher: options.fetcher } : {}),
    ...(options.now !== undefined ? { now: options.now } : {}),
  });
}
