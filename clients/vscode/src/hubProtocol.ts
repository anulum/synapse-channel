// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — strict editor projection of hub wire frames

/** Decode the bounded hub frames consumed by editor integrations. */

import {
  isJsonRecord,
  nonEmptyString,
  parseHubEnvelope,
  type HubEnvelopeError,
} from "./hubJson.js";

export { MAX_HUB_FRAME_BYTES, MAX_HUB_JSON_DEPTH } from "./hubJson.js";

/** Current wire version advertised by the matching Python hub. */
export const EDITOR_WIRE_PROTOCOL_VERSION = 2;

/** Board fields rendered by the editor. */
export interface HubTask {
  taskId: string;
  status: string;
  title: string;
}

/** Claim fields required for scope rendering and exact release correlation. */
export interface HubClaim {
  taskId: string;
  owner: string;
  worktree: string;
  paths: string[];
}

/** Initial authenticated handshake. */
export interface HubWelcomeFrame {
  kind: "welcome";
  agents: string[];
  peerProtocolVersion: number | null;
}

/** Complete live roster from a snapshot or presence event. */
export interface HubRosterFrame {
  kind: "roster";
  agents: string[];
}

/** Complete board projection. */
export interface HubBoardFrame {
  kind: "board";
  tasks: HubTask[];
}

/** Complete active-claim projection. */
export interface HubStateFrame {
  kind: "state";
  claims: HubClaim[];
  generatedAt: number | null;
}

/** Mutation event requiring a fresh authoritative state query. */
export interface HubStateChangedFrame {
  kind: "state-changed";
  operation: "claim" | "release";
  taskId: string;
}

/** Additive frame outside the editor's current projection. */
export interface HubIgnoredFrame {
  kind: "ignored";
  wireType: string;
}

/** A validated frame the editor understands or can safely ignore. */
export type HubFrame =
  | HubWelcomeFrame
  | HubRosterFrame
  | HubBoardFrame
  | HubStateFrame
  | HubStateChangedFrame
  | HubIgnoredFrame;

/** Safe failure categories; raw peer data is never reflected. */
export type HubDecodeError =
  | HubEnvelopeError
  | "invalid-known-frame";

/** Result of decoding one complete WebSocket text frame. */
export type HubDecodeResult =
  | { ok: true; frame: HubFrame }
  | { ok: false; error: HubDecodeError };

function stringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    return undefined;
  }
  return [...value];
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function protocolVersion(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) ? value : null;
}

function taskProjection(value: unknown): HubTask | undefined {
  if (!isJsonRecord(value)) {
    return undefined;
  }
  const taskId = nonEmptyString(value["task_id"]);
  const status = value["status"] === undefined ? "open" : nonEmptyString(value["status"]);
  const title = value["title"] === undefined ? "" : nonEmptyString(value["title"]);
  if (taskId === undefined || status === undefined || title === undefined) {
    return undefined;
  }
  return { taskId, status, title };
}

function claimProjection(value: unknown): HubClaim | undefined {
  if (!isJsonRecord(value)) {
    return undefined;
  }
  const taskId = nonEmptyString(value["task_id"]);
  const owner = nonEmptyString(value["owner"]);
  const paths = stringArray(value["paths"]);
  const rawWorktree = value["worktree"];
  const worktree = rawWorktree === undefined
    ? ""
    : typeof rawWorktree === "string" ? rawWorktree : undefined;
  if (taskId === undefined || owner === undefined || paths === undefined || worktree === undefined) {
    return undefined;
  }
  return { taskId, owner, worktree, paths };
}

function projectionArray<T>(
  value: unknown,
  project: (item: unknown) => T | undefined,
): T[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }
  const projected = value.map(project);
  return projected.some((item) => item === undefined) ? undefined : projected as T[];
}

function invalidKnownFrame(): HubDecodeResult {
  return { ok: false, error: "invalid-known-frame" };
}

/** Decode one hub WebSocket frame without trusting optional or unknown fields. */
export function decodeHubFrame(raw: string): HubDecodeResult {
  const parsed = parseHubEnvelope(raw);
  if (!parsed.ok) {
    return parsed;
  }
  const envelope = parsed.value;
  const wireType = envelope["type"] as string;
  if (wireType === "welcome") {
    const agents = stringArray(envelope["online_agents"]);
    return agents === undefined
      ? invalidKnownFrame()
      : {
          ok: true,
          frame: {
            kind: "welcome",
            agents,
            peerProtocolVersion: protocolVersion(envelope["protocol_version"]),
          },
        };
  }
  if (wireType === "who_snapshot" || wireType === "presence_update") {
    const agents = stringArray(envelope["online_agents"]);
    return agents === undefined
      ? invalidKnownFrame()
      : { ok: true, frame: { kind: "roster", agents } };
  }
  if (wireType === "board_snapshot") {
    const board = envelope["board"];
    const tasks = isJsonRecord(board)
      ? projectionArray(board["tasks"], taskProjection)
      : undefined;
    return tasks === undefined
      ? invalidKnownFrame()
      : { ok: true, frame: { kind: "board", tasks } };
  }
  if (wireType === "state_snapshot") {
    const snapshot = envelope["snapshot"];
    const validSnapshot = isJsonRecord(snapshot) ? snapshot : undefined;
    const claims = validSnapshot === undefined
      ? undefined
      : projectionArray(validSnapshot["active_claims"], claimProjection);
    const generatedAt = validSnapshot === undefined
      ? null
      : finiteNumber(validSnapshot["generated_at"]);
    return claims === undefined
      ? invalidKnownFrame()
      : {
          ok: true,
          frame: {
            kind: "state",
            claims,
            generatedAt,
          },
        };
  }
  if (wireType === "claim_granted" || wireType === "release_granted") {
    const taskId = nonEmptyString(envelope["task_id"]);
    return taskId === undefined
      ? invalidKnownFrame()
      : {
          ok: true,
          frame: {
            kind: "state-changed",
            operation: wireType === "claim_granted" ? "claim" : "release",
            taskId,
          },
        };
  }
  return { ok: true, frame: { kind: "ignored", wireType } };
}
