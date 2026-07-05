// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — focus lens, board report, and preference tests

import { describe, expect, it } from "vitest";
import type { BoardTask, DependencyChip } from "../src/lib/board";
import { renderBoardReport } from "../src/lib/boardReport";
import type { ClaimView } from "../src/lib/claims";
import { focusClaims, focusTasks } from "../src/lib/focus";
import { readPref, writePref } from "../src/lib/prefs";
import type { ThemeStorage } from "../src/lib/theme";
import type { ClaimRecord } from "../src/types";

function claimView(owner: string, taskId: string): ClaimView {
  const record: ClaimRecord = {
    task_id: taskId,
    owner,
    lease_expires_at: null,
    paths: [],
    stale: false,
    git: null,
  };
  return { claim: record, urgency: "held", inConflict: false, secondsToExpiry: null };
}

function task(taskId: string, overrides: Partial<BoardTask> = {}): BoardTask {
  return { taskId, title: "", status: "open", bucket: "open", dependsOn: [], unblocks: [], ...overrides };
}

function dep(taskId: string, satisfied: boolean, missing = false): DependencyChip {
  return { taskId, status: satisfied ? "done" : "open", satisfied, missing };
}

describe("focusClaims / focusTasks", () => {
  const claims = [claimView("me", "t1"), claimView("other", "t2")];

  it("passes everything through when the lens is off", () => {
    expect(focusClaims(claims, "")).toHaveLength(2);
    expect(focusTasks([task("t1")], claims, "")).toHaveLength(1);
  });

  it("narrows claims to the owner and tasks to the held orbit", () => {
    expect(focusClaims(claims, "me").map((view) => view.claim.task_id)).toEqual(["t1"]);
    const tasks = [
      task("t1"),
      task("t2"),
      task("gated", { dependsOn: [dep("t1", false)] }),
      task("upstream", { bucket: "done", unblocks: ["t1"] }),
      task("unrelated"),
    ];
    expect(focusTasks(tasks, claims, "me").map((entry) => entry.taskId)).toEqual([
      "t1",
      "gated",
      "upstream",
    ]);
  });
});

describe("renderBoardReport", () => {
  it("states scope and counts, renders buckets in triage order with verdicts", () => {
    const report = renderBoardReport(
      [
        task("open-1", { title: "Open one" }),
        task("blocked-1", {
          bucket: "blocked",
          dependsOn: [dep("ghost", false, true), dep("base", true), dep("slow", false)],
        }),
        task("done-1", { bucket: "done", unblocks: ["open-1"] }),
      ],
      "focus me",
      "2026-07-05T02:50:00.000Z",
    );
    expect(report).toContain("- scope: focus me");
    expect(report).toContain("- tasks: 3");
    expect(report.indexOf("## blocked · 1")).toBeLessThan(report.indexOf("## open · 1"));
    expect(report.indexOf("## open · 1")).toBeLessThan(report.indexOf("## done · 1"));
    expect(report).toContain("ghost (missing), base (satisfied), slow (waiting)");
    expect(report).toContain("— unblocked open-1");
    expect(report).toContain("— Open one");
  });

  it("states an empty shown board honestly", () => {
    expect(renderBoardReport([], "full board", "t")).toContain("The shown board is empty.");
  });
});

describe("prefs", () => {
  const THROWING: ThemeStorage = {
    getItem() {
      throw new Error("denied");
    },
    setItem() {
      throw new Error("denied");
    },
  };

  it("reads and writes through a working storage, shrugs off a throwing one", () => {
    const store: Record<string, string> = {};
    const storage: ThemeStorage = {
      getItem: (key) => store[key] ?? null,
      setItem: (key, value) => {
        store[key] = value;
      },
    };
    writePref(storage, "k", "v");
    expect(readPref(storage, "k")).toBe("v");
    expect(readPref(storage, "absent")).toBeNull();
    expect(readPref(THROWING, "k")).toBeNull();
    expect(() => writePref(THROWING, "k", "v")).not.toThrow();
  });
});
