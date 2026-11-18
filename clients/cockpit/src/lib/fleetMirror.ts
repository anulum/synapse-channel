// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — the Fleet mirror: its access descriptor, its strict parser and its scope's feed

// The dashboard serves Fleet's version-1 export at /fleet-observed.json to a
// principal on the observer list, and answers /fleet-observed-access.json with
// {"version":1,"observe":true,"advisory":true} to the same principal. The
// export is advisory: Fleet's view of the hubs it drains, never this hub's
// state. The parser follows the server's contract (fleet_mirror_contract.py):
// the envelope and the snapshot are read strictly, and a peer or task row that
// does not read is counted and left out, so the panel can say how many.

import { authenticatedFetch } from "./auth";

/** The staleness bound of an export, in milliseconds (the Core owner's starting value). */
export const MIRROR_STALE_MS = 5 * 60 * 1000;

/** The mirror's poll cadence in its scope, in milliseconds. */
export const MIRROR_POLL_MS = 10_000;

/** The access descriptor's poll cadence, in milliseconds. */
export const MIRROR_ACCESS_POLL_MS = 60_000;

const SOURCE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const DRAIN_LIMITS = new Set(["pages", "events", "wall_time"]);
const ENVELOPE_KEYS = ["advisory", "exported_at", "snapshot", "source_id", "version"];
const SNAPSHOT_KEYS = ["advisory", "generated_at", "peers", "progress_notes", "tasks"];
const PEER_KEYS = [
  "budget_exhausted_reason",
  "caught_up",
  "consecutive_failures",
  "cursor",
  "events",
  "last_success_at",
  "peer_id",
  "status_written_at",
];
const TASK_KEYS = ["board_conflict", "board_provenance", "claim_hub", "claimed_by", "status", "task_id", "title"];

/** One hub Fleet drains, as the export reports its drain. */
export interface MirrorPeer {
  readonly peerId: string;
  readonly cursor: number;
  readonly events: number;
  /** Epoch seconds of the last successful drain, or null. */
  readonly lastSuccessAt: number | null;
  /** Failed drains in a row, or null when Fleet does not say. */
  readonly consecutiveFailures: number | null;
  readonly caughtUp: boolean | null;
  /** The limit a drain hit (`pages`, `events`, `wall_time`), or null. */
  readonly drainLimit: string | null;
}

/** One task of Fleet's board, with which hub holds its claim and any conflict evidence. */
export interface MirrorTask {
  readonly taskId: string;
  readonly status: string;
  readonly title: string;
  readonly claimedBy: string | null;
  readonly claimHub: string | null;
  /** Where the row came from, as Fleet records it; null when it does not say. */
  readonly provenance: Readonly<Record<string, unknown>> | null;
  /** The conflict's evidence, or null when the row has none. */
  readonly conflict: Readonly<Record<string, unknown>> | null;
}

/** A parsed export: who wrote it, when, and what it observed. */
export interface FleetMirror {
  readonly sourceId: string;
  /** Epoch seconds the export was written. */
  readonly exportedAt: number;
  /** Epoch seconds Fleet generated the snapshot. */
  readonly generatedAt: number;
  /** Fleet's own advisory sentence. */
  readonly advisoryText: string;
  readonly peers: readonly MirrorPeer[];
  readonly tasks: readonly MirrorTask[];
  readonly progressNotes: number;
  /** Peer and task rows that did not read and were left out. */
  readonly skippedRows: number;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function sameKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const own = Object.keys(value).sort();
  return own.length === keys.length && own.every((key, index) => key === keys[index]);
}

function count(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function instant(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function optionalInstant(value: unknown): value is number | null {
  return value === null || instant(value);
}

function optionalText(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function evidence(value: unknown): value is Record<string, unknown> | null {
  return value === null || isObject(value);
}

function parsePeer(raw: unknown): MirrorPeer | null {
  if (!isObject(raw) || !sameKeys(raw, PEER_KEYS)) return null;
  const failures = raw["consecutive_failures"];
  const caughtUp = raw["caught_up"];
  const limit = raw["budget_exhausted_reason"];
  if (
    typeof raw["peer_id"] !== "string" ||
    raw["peer_id"] === "" ||
    !count(raw["cursor"]) ||
    !count(raw["events"]) ||
    !(failures === null || count(failures)) ||
    !optionalInstant(raw["last_success_at"]) ||
    !optionalInstant(raw["status_written_at"]) ||
    !(caughtUp === null || typeof caughtUp === "boolean") ||
    !(limit === null || (typeof limit === "string" && DRAIN_LIMITS.has(limit)))
  ) {
    return null;
  }
  return {
    peerId: raw["peer_id"],
    cursor: raw["cursor"],
    events: raw["events"],
    lastSuccessAt: raw["last_success_at"],
    consecutiveFailures: failures,
    caughtUp,
    drainLimit: limit,
  };
}

function parseTask(raw: unknown): MirrorTask | null {
  if (!isObject(raw) || !sameKeys(raw, TASK_KEYS)) return null;
  const provenance = raw["board_provenance"];
  const conflict = raw["board_conflict"];
  if (
    typeof raw["task_id"] !== "string" ||
    raw["task_id"] === "" ||
    typeof raw["status"] !== "string" ||
    typeof raw["title"] !== "string" ||
    !optionalText(raw["claimed_by"]) ||
    !optionalText(raw["claim_hub"]) ||
    !evidence(provenance) ||
    !evidence(conflict)
  ) {
    return null;
  }
  return {
    taskId: raw["task_id"],
    status: raw["status"],
    title: raw["title"],
    claimedBy: raw["claimed_by"],
    claimHub: raw["claim_hub"],
    provenance,
    conflict,
  };
}

function parseRows<T>(
  raw: unknown,
  limit: number,
  parse: (row: unknown) => T | null,
  key: (row: T) => string,
): { readonly rows: T[]; readonly skipped: number } | null {
  if (!Array.isArray(raw) || raw.length > limit) return null;
  const rows: T[] = [];
  const seen = new Set<string>();
  let skipped = 0;
  for (const item of raw) {
    const row = parse(item);
    if (row === null || seen.has(key(row))) {
      skipped += 1;
      continue;
    }
    seen.add(key(row));
    rows.push(row);
  }
  return { rows, skipped };
}

/**
 * Parse one export, or return null when its envelope or snapshot is not the
 * version-1 contract. A row that does not read is left out and counted.
 */
export function parseFleetMirror(raw: unknown): FleetMirror | null {
  if (!isObject(raw) || !sameKeys(raw, ENVELOPE_KEYS) || raw["version"] !== 1) return null;
  const source = raw["source_id"];
  const snapshot = raw["snapshot"];
  if (
    typeof source !== "string" ||
    !SOURCE_ID.test(source) ||
    raw["advisory"] !== true ||
    !instant(raw["exported_at"]) ||
    !isObject(snapshot) ||
    !sameKeys(snapshot, SNAPSHOT_KEYS) ||
    typeof snapshot["advisory"] !== "string" ||
    !instant(snapshot["generated_at"]) ||
    !count(snapshot["progress_notes"])
  ) {
    return null;
  }
  const peers = parseRows(snapshot["peers"], 4096, parsePeer, (row) => row.peerId);
  const tasks = parseRows(snapshot["tasks"], 10000, parseTask, (row) => row.taskId);
  if (peers === null || tasks === null) return null;
  return {
    sourceId: source,
    exportedAt: raw["exported_at"],
    generatedAt: snapshot["generated_at"],
    advisoryText: snapshot["advisory"],
    peers: peers.rows,
    tasks: tasks.rows,
    progressNotes: snapshot["progress_notes"],
    skippedRows: peers.skipped + tasks.skipped,
  };
}

/** Return whether the access descriptor offers this principal the mirror scope. */
export function parseMirrorAccess(raw: unknown): boolean | null {
  if (!isObject(raw) || raw["version"] !== 1) return null;
  return raw["observe"] === true && raw["advisory"] === true;
}

/** The export's age and whether it is past the staleness bound. */
export interface MirrorFreshness {
  readonly ageSeconds: number;
  readonly stale: boolean;
}

/** Return how old an export is at ``nowMs``; a future time reads as age zero. */
export function mirrorFreshness(exportedAt: number, nowMs: number, boundMs = MIRROR_STALE_MS): MirrorFreshness {
  const ageMs = Math.max(0, nowMs - exportedAt * 1000);
  return { ageSeconds: Math.floor(ageMs / 1000), stale: ageMs > boundMs };
}

/** The tasks Fleet sees as claimed, grouped by the hub that holds each claim. */
export interface MirrorClaimGroup {
  readonly hub: string;
  readonly tasks: readonly MirrorTask[];
}

/** Group the claimed tasks by ``claim_hub``; a claim without a hub is grouped under "". */
export function mirrorClaimGroups(mirror: FleetMirror): MirrorClaimGroup[] {
  const groups = new Map<string, MirrorTask[]>();
  for (const task of mirror.tasks) {
    if (task.claimedBy === null) continue;
    const hub = task.claimHub ?? "";
    const rows = groups.get(hub);
    if (rows === undefined) groups.set(hub, [task]);
    else rows.push(task);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([hub, tasks]) => ({ hub, tasks: [...tasks].sort((x, y) => x.taskId.localeCompare(y.taskId)) }));
}

/** Return the board's rows in a fixed order: conflicts first, then by task id. */
export function mirrorBoard(mirror: FleetMirror): MirrorTask[] {
  return [...mirror.tasks].sort(
    (a, b) => Number(b.conflict !== null) - Number(a.conflict !== null) || a.taskId.localeCompare(b.taskId),
  );
}

/** One short line of a conflict's or a provenance's evidence, keys in order. */
export function evidenceLine(value: Readonly<Record<string, unknown>> | null): string {
  if (value === null) return "";
  return Object.keys(value)
    .sort()
    .map((key) => `${key}: ${typeof value[key] === "string" ? String(value[key]) : JSON.stringify(value[key])}`)
    .join(" · ");
}

/** What the mirror's scope shows about its last read. */
export type MirrorStatus = "connecting" | "live" | "absent" | "incompatible" | "unavailable" | "withdrawn" | "error";

export interface MirrorState {
  /** The last good export, kept across a 503 and marked stale. */
  readonly mirror: FleetMirror | null;
  readonly status: MirrorStatus;
  /** Epoch milliseconds of the last good read, or null. */
  readonly fetchedAt: number | null;
}

export interface MirrorFeed {
  subscribe(listener: (state: MirrorState) => void): () => void;
  stop(): void;
}

export interface MirrorFeedOptions {
  readonly url?: string;
  readonly pollMs?: number;
  readonly fetcher?: typeof fetch;
  readonly now?: () => number;
}

/** Map an answer of /fleet-observed.json to the scope's status; 200 is handled apart. */
export function mirrorStatusOf(httpStatus: number): MirrorStatus {
  if (httpStatus === 404) return "absent";
  if (httpStatus === 403) return "withdrawn";
  if (httpStatus === 409) return "incompatible";
  if (httpStatus === 503) return "unavailable";
  return "error";
}

/**
 * Poll /fleet-observed.json alone. A 403 (the grant ended or changed between
 * the read and the answer) withdraws the scope and stops polling; a 503
 * keeps the last good export; a 409 says the export's version is not supported.
 */
export function createMirrorFeed(options: MirrorFeedOptions = {}): MirrorFeed {
  const fetcher = options.fetcher ?? authenticatedFetch;
  const now = options.now ?? Date.now;
  const url = options.url ?? "/fleet-observed.json";
  const pollMs = options.pollMs ?? MIRROR_POLL_MS;
  const listeners = new Set<(state: MirrorState) => void>();
  let state: MirrorState = { mirror: null, status: "connecting", fetchedAt: null };
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  let stopped = false;

  const publish = (next: MirrorState): void => {
    state = next;
    for (const listener of listeners) listener(state);
  };

  const poll = async (): Promise<void> => {
    controller = new AbortController();
    let next: MirrorState;
    try {
      const response = await fetcher(url, { signal: controller.signal });
      if (response.ok) {
        const mirror = parseFleetMirror(await response.json());
        next = mirror === null ? { ...state, status: "error" } : { mirror, status: "live", fetchedAt: now() };
      } else {
        next = { ...state, status: mirrorStatusOf(response.status) };
      }
    } catch {
      if (stopped) return;
      next = { ...state, status: "error" };
    }
    if (stopped) return;
    publish(next);
    if (next.status !== "withdrawn") timer = setTimeout(() => void poll(), pollMs);
  };

  void poll();
  return {
    subscribe(listener) {
      listeners.add(listener);
      listener(state);
      return () => {
        listeners.delete(listener);
      };
    },
    stop() {
      stopped = true;
      if (timer !== undefined) clearTimeout(timer);
      controller?.abort();
      listeners.clear();
    },
  };
}
