// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — typed incident evidence and bounded mutation tests

import { describe, expect, it } from "vitest";

import { createIncidentDraft } from "../src/lib/incidentDraftStore";
import {
  evidenceFromSelection,
  incidentEvidenceKey,
  withIncidentEvidence,
  withIncidentText,
} from "../src/lib/incidentEvidence";
import {
  INCIDENT_EVIDENCE_LIMIT,
  INCIDENT_HYPOTHESIS_LIMIT,
  INCIDENT_NOTES_LIMIT,
  INCIDENT_TITLE_LIMIT,
} from "../src/lib/incidentWorkspaceTypes";
import type { CockpitSelection, ReplayState } from "../src/lib/workspace";

const NOW = Date.parse("2026-07-22T14:00:00.000Z");
const LATER = NOW + 1_000;

function draft() {
  return createIncidentDraft(NOW, "incident-7");
}

function evidence(
  selection: CockpitSelection = { kind: "event", seq: 42 },
  replay: ReplayState = { mode: "history", at: 42 },
) {
  return evidenceFromSelection(selection, replay, NOW);
}

describe("incident evidence identity", () => {
  it("keys and labels every typed selection without a free-text join", () => {
    const selections: readonly CockpitSelection[] = [
      { kind: "event", seq: 42 },
      { kind: "task", id: "SCH-42" },
      { kind: "agent", id: "fleet/alpha" },
      { kind: "project", id: "fleet" },
      { kind: "route", source: "fleet/alpha", target: "fleet/beta" },
    ];
    expect(selections.map(incidentEvidenceKey)).toEqual([
      "event:42",
      "task:SCH-42",
      "agent:fleet/alpha",
      "project:fleet",
      "route:fleet/alpha->fleet/beta",
    ]);
    expect(selections.map((selection) => evidence(selection, { mode: "live" }).label)).toEqual([
      "sequence 42",
      "SCH-42",
      "fleet/alpha",
      "fleet",
      "fleet/alpha → fleet/beta",
    ]);
  });

  it("captures live, historical, and comparison context verbatim", () => {
    expect(evidence({ kind: "event", seq: 1 }, { mode: "live" }).replay).toEqual({ mode: "live" });
    expect(evidence({ kind: "event", seq: 1 }, { mode: "history", at: 9 }).replay).toEqual({
      mode: "history",
      at: 9,
    });
    expect(evidence({ kind: "event", seq: 1 }, { mode: "compare", a: 2, b: 9 }).replay).toEqual({
      mode: "compare",
      a: 2,
      b: 9,
    });
  });
});

describe("incident draft mutations", () => {
  it("edits, deduplicates, removes, and bounds one local draft", () => {
    const base = draft();
    const titled = withIncidentText(base, "title", "x".repeat(INCIDENT_TITLE_LIMIT + 3), LATER);
    const hypothesised = withIncidentText(
      titled,
      "hypothesis",
      "h".repeat(INCIDENT_HYPOTHESIS_LIMIT + 3),
      LATER,
    );
    const noted = withIncidentText(
      hypothesised,
      "notes",
      "n".repeat(INCIDENT_NOTES_LIMIT + 3),
      LATER,
    );
    expect(noted.title).toHaveLength(INCIDENT_TITLE_LIMIT);
    expect(noted.hypothesis).toHaveLength(INCIDENT_HYPOTHESIS_LIMIT);
    expect(noted.notes).toHaveLength(INCIDENT_NOTES_LIMIT);

    const item = evidence();
    const added = withIncidentEvidence(noted, item, null, LATER);
    const duplicate = withIncidentEvidence(added, item, null, LATER);
    expect(duplicate.evidence).toHaveLength(1);
    expect(withIncidentEvidence(duplicate, null, item.key, LATER).evidence).toEqual([]);

    let full = base;
    for (let seq = 0; seq < INCIDENT_EVIDENCE_LIMIT + 1; seq += 1) {
      full = withIncidentEvidence(full, evidence({ kind: "event", seq }), null, LATER);
    }
    expect(full.evidence).toHaveLength(INCIDENT_EVIDENCE_LIMIT);
  });
});
