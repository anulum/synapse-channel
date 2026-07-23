// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — stable facade for the hub-attested event feed

/**
 * Public event-tail contract. Parsing, semantic projection, and polling own
 * separate failure boundaries while consumers retain this stable import path.
 */
export { mapStoredEvent } from "./eventProjection";
export {
  parseStoredEvent,
  parseTail,
  type ParsedEventsTail,
  type StoredEvent,
} from "./eventTailParser";
export {
  createEventsTailSource,
  type EventsTailOptions,
  type EventsTailSource,
  type SpineProvenance,
} from "./eventsTailSource";
