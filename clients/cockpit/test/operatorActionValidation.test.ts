// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — governed action codec and local validation tests

import { describe, expect, it } from "vitest";

import {
  parseDependencyIds,
  parseOperatorOutcome,
  validateTaskDeclaration,
  validateTaskUpdate,
} from "../src/lib/operatorActionValidation";

describe("operator outcome contract", () => {
  it("accepts only the expected strict document while tolerating action-specific fields", () => {
    expect(
      parseOperatorOutcome(
        {
          action: "task",
          status: "accepted",
          detail: "recorded",
          ok: true,
          id: "T-1",
        },
        "task",
      ),
    ).toEqual({
      action: "task",
      status: "accepted",
      detail: "recorded",
      ok: true,
    });
    expect(
      parseOperatorOutcome(
        { action: "message", status: "accepted", detail: "", ok: true },
        "task",
      ),
    ).toBeNull();
    expect(
      parseOperatorOutcome({ action: "task", status: "", detail: "", ok: true }, "task"),
    ).toBeNull();
    expect(
      parseOperatorOutcome({ action: "task", status: 7, detail: "", ok: true }, "task"),
    ).toBeNull();
    expect(
      parseOperatorOutcome(
        { action: "task", status: "accepted", detail: 7, ok: true },
        "task",
      ),
    ).toBeNull();
    expect(
      parseOperatorOutcome(
        { action: "task", status: "accepted", detail: "", ok: "yes" },
        "task",
      ),
    ).toBeNull();
    expect(parseOperatorOutcome(["task"], "task")).toBeNull();
    expect(parseOperatorOutcome(null, "task")).toBeNull();
  });

  it("parses dependencies and validates both task forms without contacting the hub", () => {
    expect(parseDependencyIds(" T-0, T-1\nT-0, , T-2 ")).toEqual(["T-0", "T-1", "T-2"]);
    expect(validateTaskDeclaration({ id: "", title: "Ship", dependsOn: [] })).toBe(
      "Task id is required.",
    );
    expect(validateTaskDeclaration({ id: "T-1", title: "  ", dependsOn: [] })).toBe(
      "Task title is required.",
    );
    expect(
      validateTaskDeclaration({ id: " T-1 ", title: "Ship", dependsOn: ["T-1"] }),
    ).toBe("A task cannot depend on itself.");
    expect(
      validateTaskDeclaration({ id: "T-1", title: "Ship", dependsOn: ["T-0"] }),
    ).toBeNull();
    expect(validateTaskUpdate({ id: "", status: "done" })).toBe("Task id is required.");
    expect(validateTaskUpdate({ id: "T-1", status: " ", note: "" })).toContain(
      "status or note",
    );
    expect(validateTaskUpdate({ id: "T-1", note: "recorded" })).toBeNull();
  });
});
