// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — stable fleet communication API facade

export { deriveCommunicationModel, projectOf } from "./communicationModel";
export type {
  CommunicationEdge,
  CommunicationModel,
  CommunicationNode,
  DeliveryHealth,
  ProjectTraffic,
} from "./communicationModel";
export { deriveConversationDetail } from "./conversationDetail";
export type {
  ConversationMessage,
  SemanticResponseEvidenceScope,
  SemanticResponseStatus,
} from "./conversationDetail";
export { layoutCommunicationWeb, matrixIdentities } from "./communicationLayout";
export type { WebLayout, WebNode } from "./communicationLayout";
