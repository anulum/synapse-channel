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
  RiskSignal,
  RiskView,
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
  };
}
