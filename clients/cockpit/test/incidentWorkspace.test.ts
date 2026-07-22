// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — local incident workspace contract tests

import { describe, expect, it } from "vitest";

import {
  buildIncidentExport,
  clearIncidentDraft,
  createIncidentDraft,
  evidenceFromSelection,
  incidentEvidenceKey,
  incidentExportFilename,
  INCIDENT_EVIDENCE_LIMIT,
  INCIDENT_HYPOTHESIS_LIMIT,
  INCIDENT_NOTES_LIMIT,
  INCIDENT_TITLE_LIMIT,
  parseIncidentDraft,
  readIncidentDraft,
  withIncidentEvidence,
  withIncidentText,
  writeIncidentDraft,
  type IncidentDraft,
} from "../src/lib/incidentWorkspace";
import type { CockpitSelection, ReplayState } from "../src/lib/workspace";

const NOW = Date.parse("2026-07-22T14:00:00.000Z");
const LATER = NOW + 1_000;

function draft(): IncidentDraft {
  return createIncidentDraft(NOW, "incident-7");
}

function evidence(
  selection: CockpitSelection = { kind: "event", seq: 42 },
  replay: ReplayState = { mode: "history", at: 42 },
) {
  return evidenceFromSelection(selection, replay, NOW);
}

function memoryStorage(initial: string | null = null) {
  let value = initial;
  return {
    getItem: () => value,
    setItem: (_key: string, next: string) => {
      value = next;
    },
    removeItem: () => {
      value = null;
    },
    current: () => value,
  };
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
    expect(evidence({ kind: "event", seq: 1 }, { mode: "history", at: 9 }).replay).toEqual({ mode: "history", at: 9 });
    expect(evidence({ kind: "event", seq: 1 }, { mode: "compare", a: 2, b: 9 }).replay).toEqual({ mode: "compare", a: 2, b: 9 });
  });
});

describe("incident draft lifecycle", () => {
  it("creates, edits, deduplicates, removes, and bounds one local draft", () => {
    const base = draft();
    expect(base).toMatchObject({
      schema: "synapse-cockpit-incident/v1",
      id: "incident-7",
      title: "",
      evidence: [],
    });
    expect(() => createIncidentDraft(NOW, "bad id")).toThrow(/bounded public identifier/u);

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

  it("round-trips through browser storage and fails closed when storage throws", () => {
    const storage = memoryStorage();
    const populated = withIncidentEvidence(draft(), evidence(), null, LATER);
    expect(writeIncidentDraft(storage, "draft", populated)).toBe(true);
    expect(readIncidentDraft(storage, "draft")).toEqual(populated);
    expect(clearIncidentDraft(storage, "draft")).toBe(true);
    expect(storage.current()).toBeNull();
    expect(readIncidentDraft(storage, "draft")).toBeNull();

    const throwing = {
      getItem: () => { throw new Error("blocked"); },
      setItem: () => { throw new Error("blocked"); },
      removeItem: () => { throw new Error("blocked"); },
    };
    expect(readIncidentDraft(throwing, "draft")).toBeNull();
    expect(writeIncidentDraft(throwing, "draft", populated)).toBe(false);
    expect(clearIncidentDraft(throwing, "draft")).toBe(false);
    expect(readIncidentDraft(memoryStorage("not-json"), "draft")).toBeNull();
  });
});

describe("incident draft parser", () => {
  it("accepts a strict draft carrying every supported selection and replay mode", () => {
    const items = [
      evidence({ kind: "event", seq: 42 }, { mode: "history", at: 42 }),
      evidence({ kind: "task", id: "SCH-42" }, { mode: "live" }),
      evidence({ kind: "agent", id: "fleet/alpha" }, { mode: "compare", a: 1, b: 42 }),
      evidence({ kind: "project", id: "fleet" }, { mode: "live" }),
      evidence({ kind: "route", source: "fleet/alpha", target: "fleet/beta" }, { mode: "live" }),
    ];
    expect(parseIncidentDraft({ ...draft(), evidence: items })).toEqual({ ...draft(), evidence: items });
  });

  it.each([
    null,
    [],
    {},
    { ...draft(), schema: "v2" },
    { ...draft(), id: "bad id" },
    { ...draft(), createdAt: 1 },
    { ...draft(), updatedAt: "x".repeat(65) },
    { ...draft(), title: "x".repeat(INCIDENT_TITLE_LIMIT + 1) },
    { ...draft(), hypothesis: "\ncontrolled" },
    { ...draft(), notes: "x".repeat(INCIDENT_NOTES_LIMIT + 1) },
    { ...draft(), evidence: "no" },
    { ...draft(), evidence: Array.from({ length: INCIDENT_EVIDENCE_LIMIT + 1 }, () => evidence()) },
    { ...draft(), evidence: [evidence(), evidence()] },
    { ...draft(), evidence: [null] },
    { ...draft(), evidence: [{ ...evidence(), key: "event:41" }] },
    { ...draft(), evidence: [{ ...evidence(), label: "similar event" }] },
    { ...draft(), evidence: [{ ...evidence(), addedAt: 1 }] },
    { ...draft(), evidence: [{ ...evidence(), selection: null }] },
    { ...draft(), evidence: [{ ...evidence(), selection: { kind: "event", seq: -1 } }] },
    { ...draft(), evidence: [{ ...evidence(), selection: { kind: "event", seq: 1.5 } }] },
    { ...draft(), evidence: [{ ...evidence(), selection: { kind: "task", id: "" } }] },
    { ...draft(), evidence: [{ ...evidence(), selection: { kind: "route", source: "a", target: "" } }] },
    { ...draft(), evidence: [{ ...evidence(), selection: { kind: "unknown" } }] },
    { ...draft(), evidence: [{ ...evidence(), replay: null }] },
    { ...draft(), evidence: [{ ...evidence(), replay: { mode: "history", at: -1 } }] },
    { ...draft(), evidence: [{ ...evidence(), replay: { mode: "compare", a: 1, b: -1 } }] },
    { ...draft(), evidence: [{ ...evidence(), replay: { mode: "unknown" } }] },
  ])("refuses malformed, expanded, or ambiguous local data %#", (value) => {
    expect(parseIncidentDraft(value)).toBeNull();
  });
});

describe("incident export", () => {
  it("states authority and evidence limits next to reproducible cockpit context", () => {
    const populated = withIncidentEvidence(draft(), evidence(), null, LATER);
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
