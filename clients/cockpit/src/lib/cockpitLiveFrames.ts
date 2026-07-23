// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — pure projection of multiplexed cockpit channel frames

import {
  parseOperatorActionsPage,
  parseReceiptsPage,
  type OperatorActionRow,
  type OperatorActionsState,
  type ReceiptRow,
  type ReceiptsState,
} from "./auditFeeds";
import { mapStoredEvent, parseTail, type SpineProvenance } from "./eventsTail";
import type { LiveChannelFrame } from "./liveTransport";
import { parseSnapshot, type SnapshotState } from "./snapshot";
import type { CockpitEvent } from "../types";

/** A snapshot frame either publishes a complete state, refreshes it, or fails. */
export type SnapshotFrameProjection =
  | { readonly kind: "publish"; readonly state: SnapshotState }
  | { readonly kind: "heartbeat"; readonly sentAt: number }
  | { readonly kind: "error"; readonly error: string };

/** Decode one snapshot-channel frame without mutating React or store state. */
export function projectSnapshotFrame(frame: LiveChannelFrame): SnapshotFrameProjection {
  if (frame.status === "unchanged") return { kind: "heartbeat", sentAt: frame.sentAt };
  if (frame.status !== "live") {
    return { kind: "error", error: frame.detail ?? `snapshot stream is ${frame.status}` };
  }
  const snapshot = parseSnapshot(frame.data);
  if (snapshot === null) return { kind: "error", error: "stream snapshot was not an object" };
  return {
    kind: "publish",
    state: { snapshot, status: "live", fetchedAt: frame.sentAt, error: null },
  };
}

/** Result of decoding the retained-event channel. */
export interface EventsFrameProjection {
  readonly mode: "tail" | "derived" | null;
  readonly provenance: SpineProvenance;
  readonly events: readonly CockpitEvent[];
}

/** Decode one events-channel frame into an activation decision and exact rows. */
export function projectEventsFrame(frame: LiveChannelFrame): EventsFrameProjection {
  if (frame.status === "absent") {
    return { mode: "derived", provenance: "absent", events: [] };
  }
  if (frame.status === "error") return { mode: null, provenance: "error", events: [] };
  const page = parseTail(frame.data);
  if (page === null) return { mode: null, provenance: "error", events: [] };
  return { mode: "tail", provenance: "hub", events: page.events.map(mapStoredEvent) };
}

function mergeRows<T extends { readonly seq: number }>(
  current: readonly T[] | null,
  incoming: readonly T[],
  retainedLimit = 100,
): readonly T[] {
  const bySequence = new Map<number, T>();
  for (const row of current ?? []) bySequence.set(row.seq, row);
  for (const row of incoming) bySequence.set(row.seq, row);
  return [...bySequence.values()]
    .sort((left, right) => right.seq - left.seq)
    .slice(0, retainedLimit);
}

function projectAuditFrame<T extends { readonly seq: number }>(
  current: {
    readonly data: readonly T[] | null;
    readonly status: ReceiptsState["status"];
    readonly fetchedAt: number | null;
    readonly error: string | null;
  },
  frame: LiveChannelFrame,
  parse: (raw: unknown) => { readonly rows: readonly T[] } | null,
): typeof current {
  if (frame.status === "absent") return { ...current, status: "absent", error: null };
  if (frame.status === "error") {
    return { ...current, status: "error", error: frame.detail ?? "stream error" };
  }
  const page = parse(frame.data);
  if (page === null) {
    return { ...current, status: "error", error: "stream payload was not parseable" };
  }
  return {
    data: mergeRows(current.data, page.rows),
    status: "live",
    fetchedAt: frame.sentAt,
    error: null,
  };
}

/** Fold one live receipts page into its retained, sequence-ordered state. */
export function projectReceiptsFrame(
  current: ReceiptsState,
  frame: LiveChannelFrame,
): ReceiptsState {
  return projectAuditFrame<ReceiptRow>(current, frame, parseReceiptsPage);
}

/** Fold one live operator-action page into its retained, sequence-ordered state. */
export function projectOperatorActionsFrame(
  current: OperatorActionsState,
  frame: LiveChannelFrame,
): OperatorActionsState {
  return projectAuditFrame<OperatorActionRow>(current, frame, parseOperatorActionsPage);
}
