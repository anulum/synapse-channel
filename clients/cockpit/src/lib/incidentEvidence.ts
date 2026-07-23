// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — typed evidence cart and bounded draft mutations

import { selectionLabel } from "./selection";
import {
  INCIDENT_EVIDENCE_LIMIT,
  INCIDENT_HYPOTHESIS_LIMIT,
  INCIDENT_NOTES_LIMIT,
  INCIDENT_TITLE_LIMIT,
  type IncidentDraft,
  type IncidentEvidence,
} from "./incidentWorkspaceTypes";
import type { CockpitSelection, ReplayState } from "./workspace";

/** Stable identity for deduplicating one typed selection in the evidence cart. */
export function incidentEvidenceKey(selection: CockpitSelection): string {
  if (selection.kind === "route") return `route:${selection.source}->${selection.target}`;
  if (selection.kind === "event") return `event:${selection.seq}`;
  return `${selection.kind}:${selection.id}`;
}

/** Capture one exact typed selection and its replay context. */
export function evidenceFromSelection(
  selection: CockpitSelection,
  replay: ReplayState,
  nowMs: number,
): IncidentEvidence {
  return {
    key: incidentEvidenceKey(selection),
    selection,
    label: selectionLabel(selection),
    addedAt: new Date(nowMs).toISOString(),
    replay,
  };
}

/** Add or remove evidence with deduplication and a hard local storage bound. */
export function withIncidentEvidence(
  draft: IncidentDraft,
  evidence: IncidentEvidence | null,
  removeKey: string | null,
  nowMs: number,
): IncidentDraft {
  let next = draft.evidence;
  if (removeKey !== null) next = next.filter((item) => item.key !== removeKey);
  if (
    evidence !== null &&
    !next.some((item) => item.key === evidence.key) &&
    next.length < INCIDENT_EVIDENCE_LIMIT
  ) {
    next = [...next, evidence];
  }
  return { ...draft, evidence: next, updatedAt: new Date(nowMs).toISOString() };
}

/** Replace the editable text fields while enforcing their public limits. */
export function withIncidentText(
  draft: IncidentDraft,
  field: "title" | "hypothesis" | "notes",
  value: string,
  nowMs: number,
): IncidentDraft {
  const limit =
    field === "title"
      ? INCIDENT_TITLE_LIMIT
      : field === "hypothesis"
        ? INCIDENT_HYPOTHESIS_LIMIT
        : INCIDENT_NOTES_LIMIT;
  return {
    ...draft,
    [field]: value.slice(0, limit),
    updatedAt: new Date(nowMs).toISOString(),
  };
}
