// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — explicit non-authoritative incident export contract

import {
  INCIDENT_DRAFT_SCHEMA,
  type IncidentDraft,
  type IncidentExport,
} from "./incidentWorkspaceTypes";
import type { ReplayState } from "./workspace";

/** Build the export without promoting a local draft to hub or audit authority. */
export function buildIncidentExport(
  draft: IncidentDraft,
  replay: ReplayState,
  hubVersion: string,
  configEpoch: string,
  nowMs: number,
): IncidentExport {
  return {
    schema: INCIDENT_DRAFT_SCHEMA,
    exported_at: new Date(nowMs).toISOString(),
    provenance: "local-operator-draft",
    authority: "not-a-hub-receipt-or-signed-audit-bundle",
    evidence_boundary: {
      association: "explicit-operator-selection-only",
      event_material: "sequence-references-only",
      replay: "captured-per-evidence-reference",
    },
    cockpit: {
      hub_version: hubVersion,
      config_epoch: configEpoch,
      current_replay: replay,
    },
    incident: draft,
  };
}

/** Filesystem-safe name for one local incident export. */
export function incidentExportFilename(draft: IncidentDraft, nowMs: number): string {
  const timestamp = new Date(nowMs).toISOString().replace(/[:.]/gu, "-").slice(0, 19);
  return `synapse-incident-${draft.id}-${timestamp}.json`;
}
