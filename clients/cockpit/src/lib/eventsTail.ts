// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the hub-attested event feed: real sequences, real timestamps

// `/events.json?since=SEQ&limit=N` serves the durable log in the multihub
// snapshot shape: events carry the hub's OWN seq and ts. When this feed is
// live it replaces the snapshot-diff derivation entirely — the spine plots
// attested history, the log's seq column is the hub's, and a causality hop
// can name the exact event instead of a task's latest. When the dashboard
// does not serve the feed (404 — no --feeds-db), the cockpit says so and the
// derivation remains the honest fallback.

import { laneOf, SEVERITY_OF } from "./events";
import type { CockpitEvent, EventKind } from "../types";

/** One stored event as the multihub wire encodes it. */
export interface StoredEvent {
  readonly seq: number;
  readonly ts: number;
  readonly kind: string;
  readonly payload: Record<string, unknown>;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

/** Parse one wire event tolerantly; a malformed entry yields seq 0 (dropped). */
export function parseStoredEvent(raw: unknown): StoredEvent {
  const event = asRecord(raw);
  return {
    seq: Math.trunc(asNumber(event["seq"])),
    ts: asNumber(event["ts"]),
    kind: asString(event["kind"]),
    payload: asRecord(event["payload"]),
  };
}

/** Parse a tail response; `null` when the payload is not an object at all. */
export function parseTail(raw: unknown): { events: StoredEvent[]; nextCursor: number } | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const payload = asRecord(raw);
  const events = Array.isArray(payload["events"])
    ? payload["events"].map(parseStoredEvent).filter((event) => event.seq > 0)
    : [];
  return { events, nextCursor: Math.trunc(asNumber(payload["next_cursor"])) };
}

/** How much of a chat payload the label keeps before an ellipsis. */
const CHAT_LABEL_LIMIT = 160;

function trimmed(text: string): string {
  return text.length > CHAT_LABEL_LIMIT ? `${text.slice(0, CHAT_LABEL_LIMIT)}…` : text;
}

/**
 * Map one stored hub event onto the cockpit event model. Every mapping keeps
 * the hub's seq and ts verbatim. A kind this build does not know renders on
 * the chatter lane under its own name — shown, never hidden, never dressed up.
 */
export function mapStoredEvent(stored: StoredEvent): CockpitEvent {
  const payload = stored.payload;
  const taskId = asString(payload["task_id"]);
  let kind: EventKind;
  let actor: string;
  let label: string;

  if (stored.kind === "claim") {
    kind = "claim";
    actor = asString(payload["owner"]);
    label = `claimed ${taskId}`;
  } else if (stored.kind === "release") {
    kind = "release";
    actor = asString(payload["owner"]);
    label = `released ${taskId}`;
  } else if (stored.kind === "ledger_progress") {
    kind = asString(payload["kind"]) === "finding" ? "finding" : "chat";
    actor = asString(payload["author"]);
    const text = trimmed(asString(payload["text"]));
    label = taskId === "" ? text : `${taskId}: ${text}`;
  } else if (stored.kind === "ledger_task") {
    kind = "task";
    actor = asString(payload["created_by"]);
    const status = asString(payload["status"]);
    label = `task ${taskId}${status === "" ? "" : ` (${status})`}`;
  } else if (stored.kind === "chat") {
    kind = "chat";
    actor = asString(payload["sender"]);
    label = trimmed(asString(payload["payload"]));
  } else if (stored.kind === "dead_letter_escalation") {
    // The hub's own blackhole alarm (0.98.x): a target's undelivered count
    // crossed the escalation threshold. Risk lane, loud — not chatter.
    kind = "conflict";
    actor = asString(payload["target"]);
    const count = payload["count"];
    label = `dead-letter escalation: ${asString(payload["target"])}${
      typeof count === "number" ? ` · ${count} undelivered` : ""
    }`;
  } else {
    kind = "chat";
    actor = "";
    label = stored.kind;
  }

  return {
    seq: stored.seq,
    ts: stored.ts,
    kind,
    lane: laneOf(kind),
    severity: SEVERITY_OF[kind],
    actor,
    label,
    taskId,
    payload: stored.payload,
  };
}

/** Where the spine's events come from right now. */
export type SpineProvenance = "connecting" | "hub" | "absent" | "error";

/** A feed of hub-attested events plus its provenance state. */
export interface EventsTailSource {
  /** Register an event listener; returns an unsubscribe handle. */
  subscribe(listener: (event: CockpitEvent) => void): () => void;
  /** Register a provenance listener (called with the current state at once). */
  subscribeMode(listener: (mode: SpineProvenance) => void): () => void;
  stop(): void;
}

export interface EventsTailOptions {
  /** Endpoint to poll; defaults to the dashboard-served `/events.json`. */
  readonly url?: string;
  /** Incremental poll cadence in milliseconds. */
  readonly pollMs?: number;
  /** Re-check cadence while the endpoint is absent. */
  readonly absentPollMs?: number;
  /** Page size for each incremental poll. */
  readonly limit?: number;
  /** How many tail events the first-contact backfill emits as history. */
  readonly historyLimit?: number;
  /** Injectable fetch for tests; defaults to the global. */
  readonly fetcher?: typeof fetch;
}

const DEFAULT_EVENTS_URL = "/events.json";
const DEFAULT_EVENTS_POLL_MS = 2_000;
const DEFAULT_ABSENT_POLL_MS = 10_000;
const DEFAULT_PAGE_LIMIT = 1_000;
const DEFAULT_HISTORY_LIMIT = 250;

/**
 * Poll the hub-attested event tail. First contact costs two requests on a log
 * of any size: `since=latest` answers the current cursor, one backfill page
 * carries the last `historyLimit` events as attested history, and incremental
 * polling proceeds from the cursor. A `404` reports `absent` and re-checks
 * slowly, so the feed comes alive the moment the operator passes `--feeds-db`;
 * any other failure reports `error` and keeps trying on the normal cadence.
 */
export function createEventsTailSource(options: EventsTailOptions = {}): EventsTailSource {
  const url = options.url ?? DEFAULT_EVENTS_URL;
  const pollMs = options.pollMs ?? DEFAULT_EVENTS_POLL_MS;
  const absentPollMs = options.absentPollMs ?? DEFAULT_ABSENT_POLL_MS;
  const limit = options.limit ?? DEFAULT_PAGE_LIMIT;
  const historyLimit = options.historyLimit ?? DEFAULT_HISTORY_LIMIT;
  const fetcher = options.fetcher ?? fetch;

  const listeners = new Set<(event: CockpitEvent) => void>();
  const modeListeners = new Set<(mode: SpineProvenance) => void>();
  let mode: SpineProvenance = "connecting";
  let cursor = 0;
  let caughtUp = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  let stopped = false;

  const setMode = (next: SpineProvenance): void => {
    if (mode === next) return;
    mode = next;
    for (const listener of modeListeners) listener(mode);
  };

  const emit = (events: readonly StoredEvent[]): void => {
    for (const stored of events) {
      const event = mapStoredEvent(stored);
      for (const listener of listeners) listener(event);
    }
  };

  const fetchPage = async (
    since: number | "latest",
    pageLimit: number,
  ): Promise<{ events: StoredEvent[]; nextCursor: number } | "absent"> => {
    controller = new AbortController();
    const response = await fetcher(`${url}?since=${since}&limit=${pageLimit}`, {
      signal: controller.signal,
    });
    if (response.status === 404) return "absent";
    if (!response.ok) throw new Error(`hub returned ${response.status}`);
    const tail = parseTail(await response.json());
    if (tail === null) throw new Error("events payload was not an object");
    return tail;
  };

  const poll = async (): Promise<void> => {
    let delay = pollMs;
    try {
      if (!caughtUp) {
        // Two requests find the tail on a log of any size: `since=latest`
        // answers the current cursor, then one backfill page carries the
        // recent history worth plotting. No walk, no flood.
        const tip = await fetchPage("latest", 1);
        if (tip === "absent") {
          setMode("absent");
          delay = absentPollMs;
          return;
        }
        if (stopped) return;
        const backfillFrom = Math.max(0, tip.nextCursor - historyLimit);
        const history = await fetchPage(backfillFrom, historyLimit);
        if (history === "absent") {
          setMode("absent");
          delay = absentPollMs;
          return;
        }
        cursor = Math.max(tip.nextCursor, history.nextCursor);
        caughtUp = true;
        setMode("hub");
        if (!stopped) emit(history.events);
        return;
      }
      const page = await fetchPage(cursor, limit);
      if (page === "absent") {
        // The operator restarted the dashboard without the store: say so.
        caughtUp = false;
        setMode("absent");
        delay = absentPollMs;
        return;
      }
      cursor = page.nextCursor;
      setMode("hub");
      if (!stopped) emit(page.events);
    } catch (cause) {
      if (stopped) return;
      setMode(caughtUp ? "error" : mode === "absent" ? "absent" : "error");
      void cause;
    } finally {
      if (!stopped) timer = setTimeout(poll, delay);
    }
  };

  void poll();

  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    subscribeMode(listener) {
      modeListeners.add(listener);
      listener(mode);
      return () => modeListeners.delete(listener);
    },
    stop() {
      stopped = true;
      if (timer !== undefined) clearTimeout(timer);
      controller?.abort();
      listeners.clear();
      modeListeners.clear();
    },
  };
}
