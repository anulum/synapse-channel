// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — tests for the VS Code extension fleet model

import { describe, expect, it } from "vitest";
import {
  boardItems,
  claimMarks,
  claimRequest,
  hubHealth,
  statusBarText,
} from "../src/fleetModel.js";

describe("hubHealth", () => {
  it("is down when the connection is closed", () => {
    expect(hubHealth(false, ["a"])).toEqual({ state: "down", label: "hub offline" });
  });

  it("is degraded when up but only waiters are present", () => {
    expect(hubHealth(true, ["alpha-rx", "beta-rx"])).toEqual({
      state: "degraded",
      label: "hub up · no live agents",
    });
  });

  it("is ok and counts only live agents", () => {
    expect(hubHealth(true, ["alpha", "alpha-rx", "beta"])).toEqual({
      state: "ok",
      label: "hub up · 2 live",
    });
  });
});

describe("boardItems", () => {
  it("drops tasks without an id and sorts by id", () => {
    const items = boardItems([
      { task_id: "T2", status: "open", title: "Two" },
      { status: "open" },
      { task_id: "T1" },
    ]);
    expect(items.map((i) => i.id)).toEqual(["T1", "T2"]);
  });

  it("labels with the title when present and defaults the status", () => {
    const [withTitle, bare] = boardItems([
      { task_id: "T1", title: "Build" },
      { task_id: "T2" },
    ]);
    expect(withTitle).toEqual({ id: "T1", status: "open", label: "T1 — Build" });
    expect(bare).toEqual({ id: "T2", status: "open", label: "T2" });
  });
});

describe("claimMarks", () => {
  it("flattens paths, flags own claims, and keeps the first owner per path", () => {
    const marks = claimMarks(
      [
        { owner: "me", paths: ["src/a.ts", ""] },
        { owner: "you", paths: ["src/b.ts", "src/a.ts"] },
        { paths: ["src/c.ts"] },
      ],
      "me",
    );
    expect(marks).toEqual([
      { path: "src/a.ts", owner: "me", mine: true },
      { path: "src/b.ts", owner: "you", mine: false },
      { path: "src/c.ts", owner: "", mine: false },
    ]);
  });

  it("tolerates claims without paths", () => {
    expect(claimMarks([{ owner: "me" }], "me")).toEqual([]);
  });
});

describe("claimRequest", () => {
  it("normalises separators and strips a leading slash", () => {
    expect(claimRequest(" T1 ", "\\src\\a.ts")).toEqual({ taskId: "T1", paths: ["src/a.ts"] });
  });

  it("yields no path for an empty file", () => {
    expect(claimRequest("T1", "")).toEqual({ taskId: "T1", paths: [] });
  });
});

describe("statusBarText", () => {
  it("uses an ok icon and appends own claim count", () => {
    expect(statusBarText({ state: "ok", label: "hub up · 1 live" }, 2)).toBe(
      "$(broadcast) SYNAPSE: hub up · 1 live · 2 mine",
    );
  });

  it("uses a warning icon when degraded and omits a zero claim count", () => {
    expect(statusBarText({ state: "degraded", label: "x" }, 0)).toBe("$(warning) SYNAPSE: x");
  });

  it("uses an error icon when down", () => {
    expect(statusBarText({ state: "down", label: "hub offline" }, 0)).toBe(
      "$(error) SYNAPSE: hub offline",
    );
  });
});
