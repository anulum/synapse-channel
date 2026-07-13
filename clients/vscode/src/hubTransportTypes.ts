// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — public editor transport contract

/** Keep the transport's public API independent of its WebSocket lifecycle. */

import { type HubConnectionState } from "./connectionState.js";
import { type HubFrame } from "./hubProtocol.js";

/** Read-only hub queries used to refresh the editor projection. */
export type HubReadRequest = "who_request" | "board_request" | "state_request";

/** Mutations currently exposed by the editor. */
export type HubMutation = "claim" | "release";

/** Result of a fail-closed mutation attempt. */
export type MutationSendResult =
  | { sent: true }
  | { sent: false; reason: string };

/** Callbacks receiving validated state and frames only. */
export interface HubTransportEvents {
  onConnectionState(state: HubConnectionState): void;
  onFrame(frame: HubFrame): void;
}
