// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — stable facade for bounded durable audit feeds

/**
 * Public audit-feed contract. Strict document validation and retained polling
 * own separate failure boundaries while consumers keep this import path.
 */
export {
  parseOperatorActionsPage,
  parseReceiptsPage,
  type AuditPage,
  type OperatorActionRow,
  type ReceiptRow,
} from "./auditFeedParser";
export {
  createOperatorActionsStore,
  createReceiptsStore,
  type AuditFeedOptions,
  type OperatorActionsState,
  type ReceiptsState,
} from "./auditFeedStore";
