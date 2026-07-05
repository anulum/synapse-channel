// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — fleet time-travel: the claims and board as of any sequence

// `/state-at.json?seq=N` replays the durable log up to N and returns the
// reconstructed state — deterministic, store-derived, judged at that
// moment's own timestamp (a lease that was live at seq N reads live). This
// module shapes that document for the panels that already know how to draw
// claims and tasks. Honest scope carried through from the server: presence
// is not journalled, so time travel reconstructs CLAIMS and the BOARD —
// the roster stays live and the banner says so, in the server's own words.

import type { BoardTask, DependencyChip } from "./board";
import type { ClaimView } from "./claims";
import type { ClaimGit, ClaimRecord } from "../types";

/** The reconstructed moment, shaped for the cockpit's panels. */
export interface FleetStateAt {
  readonly asOfSeq: number;
  readonly logEndSeq: number;
  /** Epoch seconds the reconstruction was judged at (the event's own ts). */
  readonly asOfTs: number;
  /** The server's honest-scope statement, verbatim. */
  readonly note: string;
  readonly claims: readonly ClaimView[];
  readonly tasks: readonly BoardTask[];
}

/** A time-travel fetch outcome; `absent` = the dashboard serves no store feeds. */
export type StateAtResult =
  | { readonly kind: "loaded"; readonly state: FleetStateAt }
  | { readonly kind: "absent" }
  | { readonly kind: "error"; readonly message: string };

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asNumber(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function claimGit(value: unknown): ClaimGit | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  return {
    branch: asString(record["branch"]),
    base: asString(record["base"]),
    auto_release_on: asString(record["auto_release_on"]),
  };
}

function claimViewAt(raw: Record<string, unknown>, asOfTs: number): ClaimView {
  const leaseRaw = raw["lease_expires_at"];
  const lease = typeof leaseRaw === "number" && Number.isFinite(leaseRaw) ? leaseRaw : null;
  // Judged at the moment's own clock, exactly as the server reconstructs.
  const secondsToExpiry = lease === null ? null : lease - asOfTs;
  const stale = secondsToExpiry !== null && secondsToExpiry <= 0;
  const record: ClaimRecord = {
    task_id: asString(raw["task_id"]),
    owner: asString(raw["owner"]),
    lease_expires_at: lease,
    paths: Array.isArray(raw["paths"]) ? raw["paths"].filter((p): p is string => typeof p === "string") : [],
    stale,
    git: claimGit(raw["git"]),
  };
  return {
    claim: record,
    urgency: stale ? "stale" : "held",
    inConflict: false, // advisory conflicts are a live computation, not journalled state
    secondsToExpiry,
  };
}

function boardTasksAt(board: Record<string, unknown>): BoardTask[] {
  const rawTasks = Array.isArray(board["tasks"]) ? board["tasks"] : [];
  const byId = new Map<string, Record<string, unknown>>();
  for (const entry of rawTasks) {
    const record = asRecord(entry);
    const taskId = asString(record["task_id"]);
    if (taskId !== "") byId.set(taskId, record);
  }
  const dependents = new Map<string, string[]>();
  const tasks: BoardTask[] = [];
  for (const [taskId, record] of byId) {
    const dependsOn: DependencyChip[] = (Array.isArray(record["depends_on"]) ? record["depends_on"] : [])
      .filter((dep): dep is string => typeof dep === "string")
      .map((dep) => {
        const target = byId.get(dep);
        const list = dependents.get(dep);
        if (list === undefined) dependents.set(dep, [taskId]);
        else list.push(taskId);
        return {
          taskId: dep,
          status: target === undefined ? "" : asString(target["status"]),
          satisfied: target !== undefined && asString(target["status"]) === "done",
          missing: target === undefined,
        };
      });
    const status = asString(record["status"]);
    const blocked = dependsOn.some((chip) => !chip.satisfied);
    tasks.push({
      taskId,
      title: asString(record["title"]),
      status,
      bucket: status === "done" ? "done" : blocked ? "blocked" : "open",
      dependsOn,
      unblocks: [],
    });
  }
  const finished = tasks.map((task) =>
    task.bucket === "done" ? { ...task, unblocks: dependents.get(task.taskId) ?? [] } : task,
  );
  const rank: Record<BoardTask["bucket"], number> = { blocked: 0, ready: 1, open: 2, done: 3 };
  finished.sort((a, b) => rank[a.bucket] - rank[b.bucket] || a.taskId.localeCompare(b.taskId));
  return finished;
}

/**
 * Shape a `/state-at.json` document. Returns null only when the payload is
 * not an object at all; a document without claims or tasks is an honest
 * empty moment, not an error.
 */
export function parseStateAt(raw: unknown): FleetStateAt | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const payload = asRecord(raw);
  const state = asRecord(payload["state"]);
  const asOfTs = asNumber(state["generated_at"], 0);
  const rawClaims = Array.isArray(state["active_claims"]) ? state["active_claims"] : [];
  return {
    asOfSeq: asNumber(payload["as_of_seq"], 0),
    logEndSeq: asNumber(payload["log_end_seq"], 0),
    asOfTs,
    note: asString(payload["note"]),
    claims: rawClaims.map((entry) => claimViewAt(asRecord(entry), asOfTs)),
    tasks: boardTasksAt(asRecord(payload["board"])),
  };
}

const STATE_AT_URL = "/state-at.json";

/** Fetch and shape the fleet's state as of `seq`. */
export async function fetchStateAt(
  seq: number,
  fetcher: typeof fetch = fetch,
  url: string = STATE_AT_URL,
): Promise<StateAtResult> {
  try {
    const response = await fetcher(`${url}?seq=${Math.max(0, Math.trunc(seq))}`);
    if (response.status === 404) return { kind: "absent" };
    if (!response.ok) return { kind: "error", message: `hub returned ${response.status}` };
    const parsed = parseStateAt(await response.json());
    if (parsed === null) return { kind: "error", message: "state-at payload was not an object" };
    return { kind: "loaded", state: parsed };
  } catch (cause) {
    return { kind: "error", message: cause instanceof Error ? cause.message : String(cause) };
  }
}
