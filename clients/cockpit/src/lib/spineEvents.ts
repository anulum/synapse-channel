// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — derive real coordination events from fleet-snapshot transitions

import type { CockpitEvent, EventKind, FleetSnapshot } from "../types";
import { laneOf, SEVERITY_OF } from "./events";
import type { SnapshotStore } from "./snapshot";

// ---------------------------------------------------------------------------
// The spine plots only observed reality. Between two successful fetches of
// `/snapshot.json` the fleet state changes in enumerable ways — an agent joins
// or leaves the roster, a claim appears, goes stale, or is released, a board
// task changes status, a progress note lands, a risk signal turns red. Each
// such transition IS a real coordination event; this module turns the pair of
// snapshots into those events. Nothing is synthesised: no transition, no event,
// a quiet spine.
//
// Provenance honesty: the hub's durable log assigns true sequence numbers, but
// the read-only snapshot endpoint does not expose them, so events derived here
// carry a local monotonic `seq` and the fetch wall-clock as `ts`. Timing is
// therefore quantised to the poll cadence. A hub-attested event feed (true
// seq + ts) is a server-side surface the cockpit will adopt when it exists.
// ---------------------------------------------------------------------------

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function recordsOf(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(asRecord) : [];
}

/** A claim's identity across snapshots: who holds which task. */
function claimKey(owner: string, taskId: string): string {
  return `${owner}\u0000${taskId}`;
}

interface ClaimIndex {
  readonly active: ReadonlyMap<string, { owner: string; taskId: string }>;
  readonly stale: ReadonlyMap<string, { owner: string; taskId: string }>;
}

function indexClaims(snapshot: FleetSnapshot): ClaimIndex {
  const active = new Map<string, { owner: string; taskId: string }>();
  for (const claim of snapshot.fleet.claims.active_claims) {
    active.set(claimKey(claim.owner, claim.task_id), { owner: claim.owner, taskId: claim.task_id });
  }
  const stale = new Map<string, { owner: string; taskId: string }>();
  for (const claim of snapshot.fleet.claims.stale_claims) {
    stale.set(claimKey(claim.owner, claim.task_id), { owner: claim.owner, taskId: claim.task_id });
  }
  return { active, stale };
}

/** Board task statuses keyed by task id, read tolerantly from the loose payload. */
function taskStatuses(snapshot: FleetSnapshot): ReadonlyMap<string, string> {
  const statuses = new Map<string, string>();
  for (const task of recordsOf(snapshot.board["tasks"])) {
    const taskId = asString(task["task_id"]);
    if (taskId !== "") statuses.set(taskId, asString(task["status"]));
  }
  return statuses;
}

/** Red risk signals keyed by category + subject, so a persisting red fires once. */
function redSignalKeys(snapshot: FleetSnapshot): ReadonlySet<string> {
  const keys = new Set<string>();
  for (const signal of snapshot.risk.signals) {
    if (signal.level === "red") keys.add(`${signal.category}\u0000${signal.subject}`);
  }
  return keys;
}

interface EventSeed {
  readonly kind: EventKind;
  readonly actor: string;
  readonly label: string;
  /** The task the transition concerns, when it names one. */
  readonly taskId?: string;
}

function presenceSeeds(previous: FleetSnapshot, next: FleetSnapshot): EventSeed[] {
  const before = new Set(previous.fleet.agents.live);
  const after = new Set(next.fleet.agents.live);
  const seeds: EventSeed[] = [];
  for (const agent of after) {
    if (!before.has(agent)) seeds.push({ kind: "presence", actor: agent, label: "joined the roster" });
  }
  for (const agent of before) {
    if (!after.has(agent)) seeds.push({ kind: "presence", actor: agent, label: "left the roster" });
  }
  return seeds;
}

function claimSeeds(previous: FleetSnapshot, next: FleetSnapshot): EventSeed[] {
  const before = indexClaims(previous);
  const after = indexClaims(next);
  const seeds: EventSeed[] = [];
  for (const [key, claim] of after.active) {
    if (!before.active.has(key) && !before.stale.has(key)) {
      seeds.push({
        kind: "claim",
        actor: claim.owner,
        label: `claimed ${claim.taskId}`,
        taskId: claim.taskId,
      });
    }
  }
  for (const [key, claim] of after.stale) {
    if (before.active.has(key)) {
      seeds.push({
        kind: "lease",
        actor: claim.owner,
        label: `lease expired on ${claim.taskId}`,
        taskId: claim.taskId,
      });
    }
  }
  for (const [key, claim] of before.active) {
    if (!after.active.has(key) && !after.stale.has(key)) {
      seeds.push({
        kind: "release",
        actor: claim.owner,
        label: `released ${claim.taskId}`,
        taskId: claim.taskId,
      });
    }
  }
  for (const [key, claim] of before.stale) {
    if (!after.active.has(key) && !after.stale.has(key)) {
      seeds.push({
        kind: "release",
        actor: claim.owner,
        label: `stale claim ${claim.taskId} cleared`,
        taskId: claim.taskId,
      });
    }
  }
  return seeds;
}

function taskSeeds(previous: FleetSnapshot, next: FleetSnapshot): EventSeed[] {
  const before = taskStatuses(previous);
  const after = taskStatuses(next);
  const seeds: EventSeed[] = [];
  for (const [taskId, status] of after) {
    const prior = before.get(taskId);
    if (prior === undefined) {
      seeds.push({ kind: "task", actor: "", label: `task ${taskId} declared (${status})`, taskId });
    } else if (prior !== status) {
      seeds.push({ kind: "task", actor: "", label: `task ${taskId}: ${prior} → ${status}`, taskId });
    }
  }
  return seeds;
}

function progressSeeds(previous: FleetSnapshot, next: FleetSnapshot): EventSeed[] {
  const before = recordsOf(previous.board["progress"]);
  const after = recordsOf(next.board["progress"]);
  // The board's progress list is append-ordered; entries beyond the previous
  // length are the notes that landed between the two fetches.
  const seeds: EventSeed[] = [];
  for (const note of after.slice(before.length)) {
    const kind: EventKind = asString(note["kind"]) === "finding" ? "finding" : "chat";
    const taskId = asString(note["task_id"]);
    const text = asString(note["text"]);
    seeds.push({
      kind,
      actor: asString(note["author"]),
      label: taskId === "" ? text : `${taskId}: ${text}`,
      taskId,
    });
  }
  return seeds;
}

function riskSeeds(previous: FleetSnapshot, next: FleetSnapshot): EventSeed[] {
  const before = redSignalKeys(previous);
  const seeds: EventSeed[] = [];
  for (const signal of next.risk.signals) {
    if (signal.level !== "red") continue;
    if (before.has(`${signal.category}\u0000${signal.subject}`)) continue;
    seeds.push({
      kind: "conflict",
      actor: "",
      label: `${signal.category}: ${signal.subject}`,
    });
  }
  return seeds;
}

/**
 * Diff two consecutive fleet snapshots into the coordination events that
 * happened between them. Events are ordered presence → claims → tasks →
 * progress → risk, carry `atMs` (the fetch wall-clock) as their timestamp,
 * and are numbered from `seqStart`. Identical snapshots yield no events.
 */
export function deriveTransitionEvents(
  previous: FleetSnapshot,
  next: FleetSnapshot,
  atMs: number,
  seqStart: number,
): CockpitEvent[] {
  const seeds = [
    ...presenceSeeds(previous, next),
    ...claimSeeds(previous, next),
    ...taskSeeds(previous, next),
    ...progressSeeds(previous, next),
    ...riskSeeds(previous, next),
  ];
  return seeds.map((seed, index) => ({
    seq: seqStart + index,
    ts: atMs / 1000,
    kind: seed.kind,
    lane: laneOf(seed.kind),
    severity: SEVERITY_OF[seed.kind],
    actor: seed.actor,
    label: seed.label,
    taskId: seed.taskId ?? "",
  }));
}

/** A feed of derived transition events plus a way to release its subscription. */
export interface TransitionEventSource {
  /** Register a listener; returns an unsubscribe handle. */
  subscribe(listener: (event: CockpitEvent) => void): () => void;
  /** Detach from the snapshot store and drop all listeners. */
  stop(): void;
}

/**
 * Bind a {@link TransitionEventSource} to a snapshot store. The first
 * successful fetch is absorbed silently as the observation baseline; every
 * later fetch is diffed against its predecessor and the resulting events are
 * delivered to subscribers. Freshness-only re-evaluations (same `fetchedAt`)
 * emit nothing.
 */
export function createSnapshotEventSource(store: SnapshotStore): TransitionEventSource {
  const listeners = new Set<(event: CockpitEvent) => void>();
  let previous: FleetSnapshot | null = null;
  let lastFetchedAt: number | null = null;
  let seq = 0;

  const detach = store.subscribe((state) => {
    if (state.snapshot === null || state.fetchedAt === null) return;
    if (state.fetchedAt === lastFetchedAt) return;
    // A null baseline is exactly the first successful observation: absorb it.
    const baseline = previous;
    previous = state.snapshot;
    lastFetchedAt = state.fetchedAt;
    if (baseline === null) return;
    const events = deriveTransitionEvents(baseline, state.snapshot, state.fetchedAt, seq + 1);
    seq += events.length;
    for (const event of events) {
      for (const listener of listeners) listener(event);
    }
  });

  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    stop() {
      detach();
      listeners.clear();
    },
  };
}
