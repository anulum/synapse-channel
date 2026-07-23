// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — stable facade for governed cockpit operator actions

/**
 * Public operator-action contract. Deterministic validation and authenticated
 * transport remain independent internal owners.
 */
export {
  declareOperatorTask,
  sendOperatorMessage,
  sendOperatorResponse,
  updateOperatorTask,
} from "./operatorActionTransport";
export {
  parseDependencyIds,
  parseOperatorOutcome,
  validateTaskDeclaration,
  validateTaskUpdate,
} from "./operatorActionValidation";
export type {
  MessageResponseInput,
  OperatorActionResult,
  OperatorOutcomeDocument,
  OperatorSendResult,
  OperatorTaskResult,
  SemanticResponseStatus,
  TaskDeclarationInput,
  TaskUpdateInput,
} from "./operatorActionTypes";
