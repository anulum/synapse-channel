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

/** Overall hub health derived from the connection and roster. */
export type HubHealthState = "ok" | "degraded" | "down";

/** A hub health verdict with a short human-readable label. */
export interface HubHealth {
  state: HubHealthState;
  label: string;
}

/**
 * Derive hub health from the connection state and the online roster.
 *
 * A closed connection is `down`; a connection with no live (non-waiter) agent is
 * `degraded` (the hub is up but nobody is working); otherwise `ok`.
 */
export function hubHealth(connected: boolean, agents: string[]): HubHealth {
  if (!connected) {
    return { state: "down", label: "hub offline" };
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

/** Raw board task shape as it arrives from the hub snapshot. */
export interface RawTask {
  task_id?: string;
  status?: string;
  title?: string;
}

/**
 * Reduce raw board tasks to sorted, display-ready board items.
 *
 * Tasks without an id are dropped; the rest are sorted by id for a stable tree.
 */
export function boardItems(tasks: RawTask[]): BoardItem[] {
  return tasks
    .filter((task) => Boolean(task.task_id))
    .map((task) => {
      const id = String(task.task_id);
      const status = task.status ?? "open";
      const title = task.title ?? "";
      return { id, status, label: title ? `${id} — ${title}` : id };
    })
    .sort((a, b) => a.id.localeCompare(b.id));
}

/** A claimed path and who holds it, flagged when the holder is this agent. */
export interface ClaimMark {
  path: string;
  owner: string;
  mine: boolean;
}

/** Raw active-claim shape as it arrives from the hub snapshot. */
export interface RawClaim {
  owner?: string;
  paths?: string[];
}

/**
 * Flatten active claims into per-path marks for gutter decorations.
 *
 * Each claimed path yields one mark tagged with its owner and whether this agent
 * (`selfName`) holds it, so the glue can colour own vs others' claims. Paths are
 * de-duplicated keeping the first owner seen, and the result is path-sorted.
 */
export function claimMarks(claims: RawClaim[], selfName: string): ClaimMark[] {
  const seen = new Map<string, ClaimMark>();
  for (const claim of claims) {
    const owner = claim.owner ?? "";
    for (const path of claim.paths ?? []) {
      if (!path || seen.has(path)) {
        continue;
      }
      seen.set(path, { path, owner, mine: owner === selfName });
    }
  }
  return [...seen.values()].sort((a, b) => a.path.localeCompare(b.path));
}

/**
 * Build the claim request for a file: a task id and the single workspace-relative
 * path. The path is normalised to forward slashes and a leading slash is dropped.
 */
export function claimRequest(taskId: string, filePath: string): { taskId: string; paths: string[] } {
  const normalised = filePath.replace(/\\/g, "/").replace(/^\/+/, "");
  return { taskId: taskId.trim(), paths: normalised ? [normalised] : [] };
}

/** Compose the status-bar text from hub health and the number of own claims. */
export function statusBarText(health: HubHealth, ownClaims: number): string {
  const icon = health.state === "ok" ? "$(broadcast)" : health.state === "degraded" ? "$(warning)" : "$(error)";
  const claims = ownClaims > 0 ? ` · ${ownClaims} mine` : "";
  return `${icon} SYNAPSE: ${health.label}${claims}`;
}
