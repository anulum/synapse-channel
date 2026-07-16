// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — public entry point for the JS/TS client

export {
  MessageType,
  buildEnvelope,
  nowSeconds,
  type Envelope,
  type EnvelopeOptions,
  type MessageTypeValue,
} from "./protocol.js";
export {
  SynapseClient,
  type SynapseClientOptions,
  type MessageHandler,
  type WebSocketLike,
  type WebSocketFactory,
} from "./client.js";
