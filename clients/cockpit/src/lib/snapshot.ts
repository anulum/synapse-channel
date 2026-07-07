// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the live fleet-snapshot data layer

import type {
  ClaimGit,
  ClaimRecord,
  FleetAgents,
  FleetClaims,
  FleetSection,
  FleetSnapshot,
  RiskSignal,
  RiskView,
  TaskGraphEdge,
  TaskGraphNode,
  TaskGraphSection,
} from "../types";

// ---------------------------------------------------------------------------
// Defensive narrowing — the hub's payload is trusted but partial payloads (an
// old hub, a truncated response) must never crash a panel. Every reader falls
// back to a safe empty rather than throwing.
// ---------------------------------------------------------------------------

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asArray(value: unknown): readonly unknown[] {
  return Array.isArray(value) ? value : [];
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asStringArray(value: unknown): string[] {
  return asArray(value)
    .filter((item): item is string => typeof item === "string")
    .slice();
}

function asRecordArray(value: unknown): Record<string, unknown>[] {
  return asArray(value).map(asRecord);
}

function asNumberOrNull(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function asIntOr(value: unknown, fallback: number): number {
  const parsed = asNumberOrNull(value);
  return parsed === null ? fallback : Math.trunc(parsed);
}

function asLevel(value: unknown): "red" | "amber" | "green" {
  return value === "red" || value === "amber" ? value : "green";
}

function parseGit(value: unknown): ClaimGit | null {
  if (typeof value !== "object" || value === null) return null;
  const git = asRecord(value);
  return {
    branch: asString(git["branch"]),
    base: asString(git["base"]) || "main",
    auto_release_on: asString(git["auto_release_on"]),
  };
}

function parseClaim(value: unknown): ClaimRecord {
  const claim = asRecord(value);
  return {
    task_id: asString(claim["task_id"]),
    owner: asString(claim["owner"]),
    lease_expires_at: asNumberOrNull(claim["lease_expires_at"]),
    paths: asStringArray(claim["paths"]),
    stale: claim["stale"] === true,
    git: parseGit(claim["git"]),
  };
}

function parseAgents(value: unknown): FleetAgents {
  const agents = asRecord(value);
  return {
    live: asStringArray(agents["live"]),
    waiters: asStringArray(agents["waiters"]),
    missing_waiters: asStringArray(agents["missing_waiters"]),
  };
}

function parseClaims(value: unknown): FleetClaims {
  const claims = asRecord(value);
  const active = asRecordArray(claims["active_claims"]).map(parseClaim);
  const stale = asRecordArray(claims["stale_claims"]).map(parseClaim);
  return {
    active: asIntOr(claims["active"], active.length),
    stale: asIntOr(claims["stale"], stale.length),
    active_claims: active,
    stale_claims: stale,
  };
}

function parseGraphNode(value: unknown): TaskGraphNode {
  const node = asRecord(value);
  return {
    task_id: asString(node["task_id"]),
    title: asString(node["title"]),
    status: asString(node["status"]),
    ready: node["ready"] === true,
  };
}

function parseGraphEdge(value: unknown): TaskGraphEdge {
  const edge = asRecord(value);
  return {
    from: asString(edge["from"]),
    to: asString(edge["to"]),
    satisfied: edge["satisfied"] === true,
    missing: edge["missing"] === true,
    from_status: asString(edge["from_status"]),
  };
}

function parseTaskGraph(value: unknown): TaskGraphSection {
  const graph = asRecord(value);
  return {
    nodes: asArray(graph["nodes"]).map(parseGraphNode),
    edges: asArray(graph["edges"]).map(parseGraphEdge),
  };
}

function parseFleet(value: unknown): FleetSection {
  const fleet = asRecord(value);
  return {
    agents: parseAgents(fleet["agents"]),
    claims: parseClaims(fleet["claims"]),
    branch_conflicts: asRecordArray(fleet["branch_conflicts"]),
    task_graph: parseTaskGraph(fleet["task_graph"]),
    receipts: asRecordArray(fleet["receipts"]),
  };
}

function parseSignal(value: unknown): RiskSignal {
  const signal = asRecord(value);
  return {
    level: asLevel(signal["level"]),
    category: asString(signal["category"]),
    subject: asString(signal["subject"]),
    detail: asString(signal["detail"]),
  };
}

/**
 * Read the hub's role bindings ({agent: [role, ...]}), tolerant field by
 * field. A dashboard from before the pass-through yields an empty map; a
 * malformed entry contributes nothing rather than a crash.
 */
function parseAgentRoles(value: unknown): Record<string, readonly string[]> {
  const record = asRecord(value);
  const roles: Record<string, readonly string[]> = {};
  for (const [agent, bound] of Object.entries(record)) {
    if (!Array.isArray(bound)) continue;
    roles[agent] = bound.filter((role): role is string => typeof role === "string");
  }
  return roles;
}

function parseRisk(value: unknown): RiskView {
  const risk = asRecord(value);
  return {
    level: asLevel(risk["level"]),
    signals: asArray(risk["signals"]).map(parseSignal),
    safe_next_work: asStringArray(risk["safe_next_work"]),
  };
}

/**
 * Shape an untrusted `/snapshot.json` payload into a {@link FleetSnapshot}.
 * Returns `null` only when the payload is not an object at all; any object,
 * however partial, yields a snapshot with safe empty defaults for missing parts.
 */
export function parseSnapshot(raw: unknown): FleetSnapshot | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const payload = asRecord(raw);
  return {
    online_agents: asStringArray(payload["online_agents"]),
    agent_roles: parseAgentRoles(payload["agent_roles"]),
    hub_version: typeof payload["hub_version"] === "string" ? payload["hub_version"] : "",
    config_epoch: typeof payload["config_epoch"] === "string" ? payload["config_epoch"] : "",
    state: asRecord(payload["state"]),
    board: asRecord(payload["board"]),
    manifest: asRecordArray(payload["manifest"]),
    fleet: parseFleet(payload["fleet"]),
    risk: parseRisk(payload["risk"]),
  };
}

// ---------------------------------------------------------------------------
// Live polling with a freshness contract. The cockpit never silently shows old
// numbers as current: once a fetch is older than `staleAfterMs`, the store
// reports `stale` so the HUD beacon can say so.
// ---------------------------------------------------------------------------

/** Connection state of the snapshot feed, for the freshness contract. */
export type SnapshotStatus = "connecting" | "live" | "stale" | "error";

/** The latest fleet snapshot plus how fresh and trustworthy it is. */
export interface SnapshotState {
  readonly snapshot: FleetSnapshot | null;
  readonly status: SnapshotStatus;
  /** Epoch milliseconds of the last successful fetch, or null before one. */
  readonly fetchedAt: number | null;
  /** Human-readable reason for the last failure, or null when healthy. */
  readonly error: string | null;
}

/** A polling feed of the fleet snapshot. */
export interface SnapshotStore {
  subscribe(listener: (state: SnapshotState) => void): () => void;
  stop(): void;
}

export interface SnapshotStoreOptions {
  /** Endpoint to poll; defaults to the dev-proxied `/snapshot.json`. */
  readonly url?: string;
  /** Poll cadence in milliseconds. */
  readonly pollMs?: number;
  /** Age past which the newest snapshot is reported `stale`. */
  readonly staleAfterMs?: number;
  /** Injectable fetch for tests; defaults to the global. */
  readonly fetcher?: typeof fetch;
  /** Injectable clock for tests; defaults to `Date.now`. */
  readonly now?: () => number;
}

const DEFAULT_URL = "/snapshot.json";
const DEFAULT_POLL_MS = 2000;
const DEFAULT_STALE_AFTER_MS = 6000;

/** Whether a snapshot fetched at `fetchedAt` is older than the stale threshold. */
function isStale(fetchedAt: number, now: number, staleAfterMs: number): boolean {
  return now - fetchedAt > staleAfterMs;
}

/**
 * Create a {@link SnapshotStore} that polls the hub's read-only snapshot on a
 * fixed cadence. Listeners receive a new {@link SnapshotState} after every poll
 * and every freshness re-evaluation. The store keeps the last good snapshot
 * across transient errors, only flipping the reported status.
 */
export function createSnapshotStore(options: SnapshotStoreOptions = {}): SnapshotStore {
  const url = options.url ?? DEFAULT_URL;
  const pollMs = options.pollMs ?? DEFAULT_POLL_MS;
  const staleAfterMs = options.staleAfterMs ?? DEFAULT_STALE_AFTER_MS;
  const fetcher = options.fetcher ?? fetch;
  const now = options.now ?? Date.now;

  const listeners = new Set<(state: SnapshotState) => void>();
  let state: SnapshotState = {
    snapshot: null,
    status: "connecting",
    fetchedAt: null,
    error: null,
  };
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  let stopped = false;

  const publish = (next: SnapshotState): void => {
    state = next;
    for (const listener of listeners) listener(state);
  };

  const poll = async (): Promise<void> => {
    controller = new AbortController();
    try {
      const response = await fetcher(url, { signal: controller.signal });
      if (!response.ok) throw new Error(`hub returned ${response.status}`);
      const snapshot = parseSnapshot(await response.json());
      if (snapshot === null) throw new Error("snapshot payload was not an object");
      if (!stopped) {
        publish({ snapshot, status: "live", fetchedAt: now(), error: null });
      }
    } catch (cause) {
      if (stopped) return;
      // Keep the last good snapshot; report why the newest poll failed and let
      // the freshness clock decide whether the held data is stale.
      const message = cause instanceof Error ? cause.message : String(cause);
      const held = state.fetchedAt;
      const status: SnapshotStatus =
        state.snapshot === null || held === null
          ? "error"
          : isStale(held, now(), staleAfterMs)
            ? "stale"
            : "live";
      publish({ snapshot: state.snapshot, status, fetchedAt: state.fetchedAt, error: message });
    } finally {
      if (!stopped) timer = setTimeout(poll, pollMs);
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

/** Re-evaluate a state's freshness against a clock, without a new fetch. */
export function withFreshness(
  state: SnapshotState,
  now: number,
  staleAfterMs = DEFAULT_STALE_AFTER_MS,
): SnapshotState {
  if (state.fetchedAt === null || state.snapshot === null) return state;
  const status: SnapshotStatus = isStale(state.fetchedAt, now, staleAfterMs) ? "stale" : "live";
  return status === state.status ? state : { ...state, status };
}
