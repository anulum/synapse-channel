// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — non-authoritative local incident export tests

import { describe, expect, it } from "vitest";

import { createIncidentDraft } from "../src/lib/incidentDraftStore";
import { evidenceFromSelection, withIncidentEvidence } from "../src/lib/incidentEvidence";
import { buildIncidentExport, incidentExportFilename } from "../src/lib/incidentExport";

const NOW = Date.parse("2026-07-22T14:00:00.000Z");
const LATER = NOW + 1_000;

describe("incident export", () => {
  it("states authority and evidence limits next to reproducible cockpit context", () => {
    const draft = createIncidentDraft(NOW, "incident-7");
    const evidence = evidenceFromSelection({ kind: "event", seq: 42 }, { mode: "history", at: 42 }, NOW);
    const populated = withIncidentEvidence(draft, evidence, null, LATER);
    const exported = buildIncidentExport(
      populated,
      { mode: "compare", a: 4, b: 42 },
      "0.99.12",
      "epoch-7",
      LATER,
    );
    expect(exported).toMatchObject({
      provenance: "local-operator-draft",
      authority: "not-a-hub-receipt-or-signed-audit-bundle",
      evidence_boundary: {
        association: "explicit-operator-selection-only",
        event_material: "sequence-references-only",
        replay: "captured-per-evidence-reference",
      },
      cockpit: {
        hub_version: "0.99.12",
        config_epoch: "epoch-7",
        current_replay: { mode: "compare", a: 4, b: 42 },
      },
      incident: populated,
    });
    expect(exported.exported_at).toBe(new Date(LATER).toISOString());
    expect(incidentExportFilename(populated, LATER)).toMatch(
      /^synapse-incident-incident-7-2026-07-22T14-00-01\.json$/u,
    );
  });
});
