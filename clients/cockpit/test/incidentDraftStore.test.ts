// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — fail-closed local incident draft persistence tests

import { describe, expect, it } from "vitest";

import {
  clearIncidentDraft,
  createIncidentDraft,
  parseIncidentDraft,
  readIncidentDraft,
  writeIncidentDraft,
} from "../src/lib/incidentDraftStore";
import { evidenceFromSelection } from "../src/lib/incidentEvidence";
import {
  INCIDENT_EVIDENCE_LIMIT,
  INCIDENT_NOTES_LIMIT,
  type IncidentDraft,
} from "../src/lib/incidentWorkspaceTypes";
import type { CockpitSelection, ReplayState } from "../src/lib/workspace";

const NOW = Date.parse("2026-07-22T14:00:00.000Z");

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

describe("incident draft storage", () => {
  it("creates a bounded version-one draft", () => {
    expect(draft()).toMatchObject({
      schema: "synapse-cockpit-incident/v1",
      id: "incident-7",
      title: "",
      evidence: [],
    });
    expect(() => createIncidentDraft(NOW, "bad id")).toThrow(/bounded public identifier/u);
  });

  it("round-trips through browser storage and fails closed when storage throws", () => {
    const storage = memoryStorage();
    const populated = { ...draft(), evidence: [evidence()] };
    expect(writeIncidentDraft(storage, "draft", populated)).toBe(true);
    expect(readIncidentDraft(storage, "draft")).toEqual(populated);
    expect(clearIncidentDraft(storage, "draft")).toBe(true);
    expect(storage.current()).toBeNull();
    expect(readIncidentDraft(storage, "draft")).toBeNull();

    const throwing = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
      removeItem: () => {
        throw new Error("blocked");
      },
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
      evidence(
        { kind: "route", source: "fleet/alpha", target: "fleet/beta" },
        { mode: "live" },
      ),
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
    { ...draft(), title: "x".repeat(121) },
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
    {
      ...draft(),
      evidence: [{ ...evidence(), selection: { kind: "route", source: "a", target: "" } }],
    },
    { ...draft(), evidence: [{ ...evidence(), selection: { kind: "unknown" } }] },
    { ...draft(), evidence: [{ ...evidence(), replay: null }] },
    { ...draft(), evidence: [{ ...evidence(), replay: { mode: "history", at: -1 } }] },
    { ...draft(), evidence: [{ ...evidence(), replay: { mode: "compare", a: 1, b: -1 } }] },
    { ...draft(), evidence: [{ ...evidence(), replay: { mode: "unknown" } }] },
  ])("refuses malformed, expanded, or ambiguous local data %#", (value) => {
    expect(parseIncidentDraft(value)).toBeNull();
  });
});
