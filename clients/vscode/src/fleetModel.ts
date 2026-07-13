// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — editor-agnostic fleet model for the VS Code extension

/**
 * Pure, editor-agnostic logic behind the VS Code / Cursor extension.
 *
 * The extension glue ({@link ../src/extension.ts}) owns the VS Code API surface
 * — commands, the status bar, the board tree, and gutter decorations — but every
 * decision it renders is computed here, where it can be unit-tested without an
 * editor host. This module turns the hub's roster, board, and claim records into
 * the small view models the glue paints.
 */

import {
  lastFrameAgeMs,
  type HubConnectionState,
} from "./connectionState.js";
import { type HubClaim, type HubTask } from "./hubProtocol.js";

/** Backward-compatible name for claim consumers inside the extension. */
export type RawClaim = HubClaim;

/** Backward-compatible name for task consumers inside the extension. */
export type RawTask = HubTask;

/** Overall hub health derived from the negotiated connection and roster. */
export type HubHealthState = "ok" | "degraded" | "warning" | "down";

/** A hub health verdict with a short human-readable label. */
export interface HubHealth {
  state: HubHealthState;
  label: string;
}

/**
 * Derive hub health from the negotiated connection state and online roster.
 *
 * A closed connection is `down`; a connection with no live (non-waiter) agent is
 * `degraded` (the hub is up but nobody is working); otherwise `ok`.
 */
export function hubHealth(
  connection: HubConnectionState,
  agents: string[],
  now: number = Date.now(),
): HubHealth {
  if (connection.phase === "identity-mismatch") {
    return { state: "down", label: "identity trust mismatch" };
  }
  if (connection.phase === "incompatible") {
    return { state: "down", label: "wire contract incompatible" };
  }
  if (connection.phase === "disconnected") {
    const suffix = connection.lastFrameAt === undefined ? "" : " · last-good state retained";
    return { state: "down", label: `hub offline${suffix}` };
  }
  if (connection.phase === "negotiating") {
    return { state: "warning", label: "negotiating hub protocol" };
  }
  if (connection.phase === "stale") {
    const age = lastFrameAgeMs(connection, now);
    const ageLabel = age === undefined ? "unknown age" : `${Math.floor(age / 1_000)}s old`;
    return { state: "warning", label: `hub stale · last update ${ageLabel}` };
  }
  const live = agents.filter((name) => !name.endsWith("-rx"));
  if (live.length === 0) {
    return { state: "degraded", label: "hub up · no live agents" };
  }
  return { state: "ok", label: `hub up · ${live.length} live` };
}

/** A board task reduced to what the tree view shows. */
export interface BoardItem {
  id: string;
  status: string;
  label: string;
}

/**
 * Reduce raw board tasks to sorted, display-ready board items.
 *
 * Tasks without an id are dropped; the rest are sorted by id for a stable tree.
 */
export function boardItems(tasks: readonly HubTask[]): BoardItem[] {
  return tasks
    .map((task) => {
      const id = task.taskId;
      const status = task.status;
      const title = task.title;
      return { id, status, label: title ? `${id} — ${title}` : id };
    })
    .sort((a, b) => a.id.localeCompare(b.id));
}

/** A claimed path and who holds it, flagged when the holder is this agent. */
export interface ClaimMark {
  worktree: string;
  path: string;
  owner: string;
  mine: boolean;
}

/**
 * Flatten active claims into per-path marks for gutter decorations.
 *
 * Each claimed path yields one mark tagged with its owner and whether this agent
 * (`selfName`) holds it, so the glue can colour own vs others' claims. Paths are
 * de-duplicated by exact worktree and path, keeping the first owner seen.
 */
export function claimMarks(claims: readonly HubClaim[], selfName: string): ClaimMark[] {
  const seen = new Map<string, ClaimMark>();
  for (const claim of claims) {
    const owner = claim.owner;
    for (const path of claim.paths) {
      const key = `${claim.worktree}\u0000${path}`;
      if (!path || seen.has(key)) {
        continue;
      }
      seen.set(key, {
        worktree: claim.worktree,
        path,
        owner,
        mine: owner === selfName,
      });
    }
  }
  return [...seen.values()].sort((a, b) =>
    a.worktree.localeCompare(b.worktree) || a.path.localeCompare(b.path),
  );
}

/** Whether retained fleet data belongs to a different hub or editor identity. */
export function hubProjectionChanged(
  previous: { uri: string; identity: string } | undefined,
  next: { uri: string; identity: string },
): boolean {
  return previous === undefined
    || previous.uri !== next.uri
    || previous.identity !== next.identity;
}

/** Compose the status-bar text from hub health and the number of own claims. */
export function statusBarText(health: HubHealth, ownClaims: number): string {
  const icon = health.state === "ok"
    ? "$(broadcast)"
    : health.state === "down"
      ? "$(error)"
      : "$(warning)";
  const claims = ownClaims > 0 ? ` · ${ownClaims} mine` : "";
  return `${icon} SYNAPSE: ${health.label}${claims}`;
}
