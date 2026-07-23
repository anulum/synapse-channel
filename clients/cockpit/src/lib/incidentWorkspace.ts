// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — stable facade for the local incident workspace

/**
 * Public incident-workspace contract. Persistence, evidence mutation, and
 * non-authoritative export generation remain independent internal owners.
 */
export {
  clearIncidentDraft,
  createIncidentDraft,
  parseIncidentDraft,
  readIncidentDraft,
  writeIncidentDraft,
} from "./incidentDraftStore";
export {
  evidenceFromSelection,
  incidentEvidenceKey,
  withIncidentEvidence,
  withIncidentText,
} from "./incidentEvidence";
export { buildIncidentExport, incidentExportFilename } from "./incidentExport";
export {
  INCIDENT_DRAFT_SCHEMA,
  INCIDENT_EVIDENCE_LIMIT,
  INCIDENT_HYPOTHESIS_LIMIT,
  INCIDENT_NOTES_LIMIT,
  INCIDENT_TITLE_LIMIT,
  type IncidentDraft,
  type IncidentEvidence,
  type IncidentExport,
} from "./incidentWorkspaceTypes";
