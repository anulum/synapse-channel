// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — cockpit domain types

/**
 * The four signal lanes of the activity spine. A quiet `risk` lane means the
 * fleet is healthy; any deflection there is what an operator watches.
 */
export type Lane = "presence" | "claims" | "task" | "risk";

/**
 * A durable-log event class, mapped from the hub's event kinds. Each class has a
 * fixed lane and a fixed semantic colour so it reads identically everywhere.
 */
export type EventKind =
  | "presence"
  | "claim"
  | "lease"
  | "release"
  | "task"
  | "chat"
  | "finding"
  | "conflict";

/** One coordination event positioned on the spine. */
export interface CockpitEvent {
  /** Durable event-log sequence; the event's identity. */
  readonly seq: number;
  /** Event timestamp (epoch seconds). */
  readonly ts: number;
  readonly kind: EventKind;
  readonly lane: Lane;
  /** 0..1 importance; drives impulse height and glyph promotion. */
  readonly severity: number;
  /** Hub-attested actor (agent name), when the event carries one. */
  readonly actor: string;
  /** Short human-readable summary for the detail panel and tooltip. */
  readonly label: string;
}

/** A source of live coordination events for the spine and log. */
export interface EventSource {
  /** Register a listener; returns an unsubscribe handle. */
  subscribe(listener: (event: CockpitEvent) => void): () => void;
  /** Release any timers or connections. */
  stop(): void;
}

/** A single git worktree claim as summarised by the hub fleet view. */
export interface ClaimGit {
  readonly branch: string;
  readonly base: string;
  readonly auto_release_on: string;
}

/**
 * One held file scope, as the hub reports it in `fleet.claims`. `stale` means the
 * lease expired but the record is still present — the strongest fleet risk.
 */
export interface ClaimRecord {
  readonly task_id: string;
  readonly owner: string;
  /** Epoch seconds the lease expires, or null when open-ended. */
  readonly lease_expires_at: number | null;
  readonly paths: readonly string[];
  readonly stale: boolean;
  readonly git: ClaimGit | null;
}

/** Live agents, `-rx` waiters, and waiters expected but absent. */
export interface FleetAgents {
  readonly live: readonly string[];
  readonly waiters: readonly string[];
  readonly missing_waiters: readonly string[];
}

/** Active and stale claim buckets split by lease freshness. */
export interface FleetClaims {
  readonly active: number;
  readonly stale: number;
  readonly active_claims: readonly ClaimRecord[];
  readonly stale_claims: readonly ClaimRecord[];
}

/** The derived fleet section the cockpit reads directly. */
export interface FleetSection {
  readonly agents: FleetAgents;
  readonly claims: FleetClaims;
  readonly branch_conflicts: readonly Record<string, unknown>[];
}

/** One triaged risk pointing back to a concrete snapshot record. */
export interface RiskSignal {
  readonly level: "red" | "amber" | "green";
  readonly category: string;
  readonly subject: string;
  readonly detail: string;
}

/** Operator triage: the worst level, the ordered signals, and safe next work. */
export interface RiskView {
  readonly level: "red" | "amber" | "green";
  readonly signals: readonly RiskSignal[];
  readonly safe_next_work: readonly string[];
}

/**
 * The read-only fleet snapshot served by `synapse dashboard` at
 * `/snapshot.json`. The `fleet` and `risk` sections the cockpit renders are
 * typed; the raw hub `state`/`board` payloads stay loose until a panel claims
 * them.
 */
export interface FleetSnapshot {
  readonly online_agents: readonly string[];
  readonly state: Record<string, unknown>;
  readonly board: Record<string, unknown>;
  readonly manifest: readonly Record<string, unknown>[];
  readonly fleet: FleetSection;
  readonly risk: RiskView;
}
