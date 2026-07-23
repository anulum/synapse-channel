// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — immutable local incident workspace contracts

import type { CockpitSelection, ReplayState } from "./workspace";

export const INCIDENT_DRAFT_SCHEMA = "synapse-cockpit-incident/v1";
export const INCIDENT_EVIDENCE_LIMIT = 64;
export const INCIDENT_TITLE_LIMIT = 120;
export const INCIDENT_HYPOTHESIS_LIMIT = 1_000;
export const INCIDENT_NOTES_LIMIT = 8_000;

/** One operator-selected evidence reference; no relationship is inferred. */
export interface IncidentEvidence {
  readonly key: string;
  readonly selection: CockpitSelection;
  readonly label: string;
  readonly addedAt: string;
  readonly replay: ReplayState;
}

/** One browser-local incident draft scoped to the authenticated principal. */
export interface IncidentDraft {
  readonly schema: typeof INCIDENT_DRAFT_SCHEMA;
  readonly id: string;
  readonly createdAt: string;
  readonly updatedAt: string;
  readonly title: string;
  readonly hypothesis: string;
  readonly notes: string;
  readonly evidence: readonly IncidentEvidence[];
}

/** Self-describing, non-authoritative document handed to an operator. */
export interface IncidentExport {
  readonly schema: typeof INCIDENT_DRAFT_SCHEMA;
  readonly exported_at: string;
  readonly provenance: "local-operator-draft";
  readonly authority: "not-a-hub-receipt-or-signed-audit-bundle";
  readonly evidence_boundary: {
    readonly association: "explicit-operator-selection-only";
    readonly event_material: "sequence-references-only";
    readonly replay: "captured-per-evidence-reference";
  };
  readonly cockpit: {
    readonly hub_version: string;
    readonly config_epoch: string;
    readonly current_replay: ReplayState;
  };
  readonly incident: IncidentDraft;
}
