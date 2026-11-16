// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the operations model: states, answers, chain parts and grouping

import { describe, expect, it } from "vitest";

import {
  ANSWER_NAMES,
  APPROVED_FRESH,
  OPERATION_STATES,
  groupByState,
  instantText,
  isRefusingAnswer,
  phaseOf,
  readState,
  type OperationRow,
} from "../src/lib/operations";

/** The tone each state of the report is drawn with; nothing here says more than the rows. */
const EXPECTED_TONES: Readonly<Record<string, string>> = {
  verified: "settled",
  sealed: "sealed",
  "ended, unreconciled": "attention",
  "outcome reported, unknown": "unknown",
  "attempt open, silent": "attention",
  "attempt open": "open",
  reconciled: "settled",
  "awaiting approval": "waiting",
  "approved, no attempt": "neutral",
  "approval rejected": "refused",
  declared: "neutral",
  "not recorded": "absent",
};

function row(operationId: string, state: string, declaredSeq: number): OperationRow {
  return {
    operationId,
    taskId: "T1",
    declarer: "P/worker",
    declaredSeq,
    state,
    latestSeq: declaredSeq + 1,
    latestKind: "answer",
    latestAt: 1_800_000_000,
  };
}

describe("readState", () => {
  it("knows every state of the report and keeps its words", () => {
    expect(Object.keys(EXPECTED_TONES)).toEqual([...OPERATION_STATES]);
    OPERATION_STATES.forEach((state, index) => {
      expect(readState(state)).toEqual({ text: state, tone: EXPECTED_TONES[state], known: true, answer: null, rank: index });
    });
  });

  it("reads a refusal by every refusing answer, after the attempt states", () => {
    const refusing = ANSWER_NAMES.filter((name) => name !== APPROVED_FRESH);
    expect(refusing).toHaveLength(10);
    for (const answer of refusing) {
      const reading = readState(`refused: ${answer}`);
      expect(reading).toMatchObject({ text: `refused: ${answer}`, tone: "refused", known: true, answer });
      expect(reading.rank).toBeGreaterThan(readState("reconciled").rank);
      expect(reading.rank).toBeLessThan(readState("awaiting approval").rank);
    }
  });

  it("marks a refusal by an answer that refuses nothing, or by a name it does not know", () => {
    expect(readState("refused: approved_fresh")).toMatchObject({ tone: "refused", known: false, answer: "approved_fresh" });
    expect(readState("refused: new_answer")).toMatchObject({ tone: "refused", known: false, answer: "new_answer" });
  });

  it("shows a state it does not know verbatim, never as a known one", () => {
    for (const text of ["success", "Verified", "", "attempt open,silent"]) {
      expect(readState(text)).toEqual({ text, tone: "unrecognised", known: false, answer: null, rank: OPERATION_STATES.length });
    }
  });
});

describe("answers and chain parts", () => {
  it("refuses with every answer but the one that opens an attempt", () => {
    expect(ANSWER_NAMES).toHaveLength(11);
    for (const name of ANSWER_NAMES) expect(isRefusingAnswer(name)).toBe(name !== "approved_fresh");
    expect(isRefusingAnswer("refused")).toBe(false);
  });

  it("places each link kind in its part of the chain, and an unknown kind nowhere", () => {
    const kinds = ["declared", "approval", "answer", "seal", "outcome", "verification"];
    expect(kinds.map(phaseOf)).toEqual(["A", "B", "C", "C", "D", "D"]);
    for (const kind of ["custody", "", "constructor", "toString"]) expect(phaseOf(kind)).toBe("?");
  });
});

describe("groupByState", () => {
  it("groups by the report's order, refusals after the attempt states, an unknown state last", () => {
    const rows = [
      row("op-1", "declared", 1),
      row("op-2", "refused: stale_epoch", 2),
      row("op-3", "mystery", 3),
      row("op-4", "verified", 4),
      row("op-5", "refused: attempt_exists", 5),
      row("op-6", "declared", 6),
      row("op-7", "attempt open, silent", 7),
    ];
    const groups = groupByState(rows);
    expect(groups.map((group) => group.state.text)).toEqual([
      "verified",
      "attempt open, silent",
      "refused: attempt_exists",
      "refused: stale_epoch",
      "declared",
      "mystery",
    ]);
    expect(groups[4]?.rows.map((item) => item.operationId)).toEqual(["op-1", "op-6"]);
    expect(groupByState([])).toEqual([]);
  });
});

describe("instantText", () => {
  it("renders an exact UTC instant, and nothing for a row that holds none", () => {
    expect(instantText(1_800_000_000.5)).toBe("2027-01-15T08:00:00.500Z");
    expect(instantText(null)).toBeNull();
  });
});
