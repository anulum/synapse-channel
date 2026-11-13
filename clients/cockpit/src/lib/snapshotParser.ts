// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — untrusted fleet-snapshot document parser

import type {
  ClaimGit,
  ClaimRecord,
  FleetAgents,
  FleetClaims,
  FleetSection,
  FleetSnapshot,
  Headline,
  RiskSignal,
  RiskView,
  SeatRow,
  SeatState,
  SnapshotBounds,
  TaskGraphEdge,
  TaskGraphNode,
  TaskGraphSection,
} from "../types";

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

function asBooleanOrNull(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
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

const SEAT_STATES: ReadonlySet<string> = new Set(["listening", "active, not listening", "silent"]);

/** One seat row; null when it lacks an identity or a known state. */
function parseSeat(value: unknown): SeatRow | null {
  const seat = asRecord(value);
  const identity = asString(seat["identity"]);
  const state = asString(seat["state"]);
  if (identity === "" || !SEAT_STATES.has(state)) return null;
  const evidence = seat["evidence"];
  // A dashboard older than the hub's reaction proof sends no reaction: no judgement, none invented.
  const reaction = asRecord(seat["reaction"]);
  return {
    identity,
    listening: seat["listening"] === true,
    active: seat["active"] === true,
    evidence: evidence === "claim" || evidence === "note" ? evidence : null,
    evidenceAt: asNumberOrNull(seat["evidence_at"]),
    state: state as SeatState,
    reaction: {
      proven: asBooleanOrNull(reaction["proven"]),
      liveWaiter: asBooleanOrNull(reaction["live_waiter"]),
      lastReactionAge: asNumberOrNull(reaction["last_reaction_age"]),
    },
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
    seats: asArray(fleet["seats"])
      .map(parseSeat)
      .filter((seat): seat is SeatRow => seat !== null),
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

const BOUND_COUNTS = [
  "tasks_total",
  "tasks_sent",
  "closed_omitted",
  "progress_total",
  "progress_sent",
  "receipts_total",
  "receipts_sent",
] as const;

type BoundCounts = [number, number, number, number, number, number, number];

function wholeCount(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

/** Read the bounded snapshot's `bounds` block; null when absent or malformed. */
export function parseBounds(value: unknown): SnapshotBounds | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const block = value as Record<string, unknown>;
  const counts = BOUND_COUNTS.map((key) => wholeCount(block[key]));
  if (counts.some((count) => count === null)) return null;
  const [tasksTotal, tasksSent, closedOmitted, progressTotal, progressSent, receiptsTotal, receiptsSent] =
    counts as BoundCounts;
  return {
    closedIncluded: block["closed_included"] === true,
    tasksTotal,
    tasksSent,
    closedOmitted,
    progressTotal,
    progressSent,
    receiptsTotal,
    receiptsSent,
  };
}

const HEADLINE_FIELDS = [
  ["agents_live", "agentsLive"],
  ["waiters_missing", "waitersMissing"],
  ["claims_active", "claimsActive"],
  ["claims_stale", "claimsStale"],
  ["tasks_open", "tasksOpen"],
  ["tasks_in_progress", "tasksInProgress"],
  ["tasks_ready", "tasksReady"],
  ["tasks_blocked", "tasksBlocked"],
  ["tasks_blocked_marked", "tasksBlockedMarked"],
  ["tasks_blocked_waiting", "tasksBlockedWaiting"],
  ["tasks_closed", "tasksClosed"],
  ["risk_signals", "riskSignals"],
  ["risk_signals_red", "riskSignalsRed"],
  ["risk_signals_amber", "riskSignalsAmber"],
  ["branch_conflicts", "branchConflicts"],
  ["peers_reachable", "peersReachable"],
  ["peers_total", "peersTotal"],
  ["seats_listening", "seatsListening"],
  ["seats_active_not_listening", "seatsActiveNotListening"],
  ["seats_silent", "seatsSilent"],
  ["seats_deaf", "seatsDeaf"],
] as const satisfies readonly (readonly [string, keyof Headline])[];

/** Read the snapshot's `headline` block; null when absent or any counter is malformed. */
export function parseHeadline(value: unknown): Headline | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const block = value as Record<string, unknown>;
  const counts: Partial<Record<keyof Headline, number>> = {};
  for (const [wire, field] of HEADLINE_FIELDS) {
    const count = wholeCount(block[wire]);
    if (count === null) return null;
    counts[field] = count;
  }
  return counts as Headline;
}

/**
 * Shape an untrusted `/snapshot.json` payload into a {@link FleetSnapshot}.
 * Returns `null` only when the payload is not an object at all; any partial
 * object yields safe empty defaults for absent or malformed fields.
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
    bounds: parseBounds(payload["bounds"]),
    headline: parseHeadline(payload["headline"]),
  };
}
