// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — on-wire message types and envelope for the JS/TS client

/**
 * The on-wire message-type vocabulary, mirroring the Python hub's
 * `synapse_channel.core.protocol.MessageType`. Values are the wire strings; do
 * not rename one without migrating every peer.
 */
export const MessageType = {
  // Agent -> hub.
  Chat: "chat",
  Heartbeat: "heartbeat",
  Claim: "claim",
  Release: "release",
  StateRequest: "state_request",
  WhoRequest: "who_request",
  HistoryRequest: "history_request",
  TaskUpdate: "task_update",
  Handoff: "handoff",
  Checkpoint: "checkpoint",
  Resource: "resource",
  LedgerTask: "ledger_task",
  LedgerTaskUpdate: "ledger_task_update",
  LedgerProgress: "ledger_progress",
  BoardRequest: "board_request",
  Advertise: "advertise",
  ManifestRequest: "manifest_request",
  Finding: "finding",
  // Hub -> agent.
  System: "system",
  Welcome: "welcome",
  PresenceUpdate: "presence_update",
  DeliveryReceipt: "delivery_receipt",
  ClaimGranted: "claim_granted",
  ClaimDenied: "claim_denied",
  ReleaseGranted: "release_granted",
  ReleaseDenied: "release_denied",
  TaskUpdated: "task_updated",
  CheckpointSaved: "checkpoint_saved",
  StateSnapshot: "state_snapshot",
  WhoSnapshot: "who_snapshot",
  BoardSnapshot: "board_snapshot",
  LedgerTaskPosted: "ledger_task_posted",
  LedgerProgressPosted: "ledger_progress_posted",
  Error: "error",
} as const;

/** A wire message-type string. */
export type MessageTypeValue = (typeof MessageType)[keyof typeof MessageType];

/**
 * One agent-side message envelope sent to the hub. The base fields mirror the
 * Python `build_envelope`; any additional protocol fields (for example
 * `task_id`, `paths`, `limit`) ride alongside them.
 */
export interface Envelope {
  sender: string;
  target: string;
  type: string;
  payload: string;
  timestamp: number;
  [field: string]: unknown;
}

/** Options accepted by {@link buildEnvelope} beyond the message type. */
export interface EnvelopeOptions {
  /** Recipient agent name, or `"all"` for a broadcast. Defaults to `"all"`. */
  target?: string;
  /** Free-form text body. Defaults to an empty string. */
  payload?: string;
  /** Override timestamp in seconds; defaults to the current wall-clock time. */
  now?: number;
  /** Additional protocol fields merged into the envelope after the base fields. */
  extra?: Record<string, unknown>;
}

/** Return the current wall-clock time in seconds, matching the Python stamp. */
export function nowSeconds(): number {
  return Date.now() / 1000;
}

/**
 * Build an agent-side message envelope, mirroring the Python `build_envelope`.
 *
 * @param sender - Name of the sending agent.
 * @param type - One of the {@link MessageType} wire strings.
 * @param options - Target, payload, timestamp override, and extra fields.
 * @returns A JSON-serialisable envelope ready for `JSON.stringify`.
 */
export function buildEnvelope(sender: string, type: string, options: EnvelopeOptions = {}): Envelope {
  return {
    sender,
    target: options.target ?? "all",
    type,
    payload: options.payload ?? "",
    timestamp: options.now ?? nowSeconds(),
    ...(options.extra ?? {}),
  };
}
