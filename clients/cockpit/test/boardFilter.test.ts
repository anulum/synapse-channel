// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — board query tests

import { describe, expect, it } from "vitest";
import type { BoardTask } from "../src/lib/board";
import {
  boardQueryConstrained,
  bucketCounts,
  filterBoard,
  matchesBoardQuery,
  OPEN_BOARD_QUERY,
  toggleBucket,
} from "../src/lib/boardFilter";

function task(taskId: string, overrides: Partial<BoardTask> = {}): BoardTask {
  return {
    taskId,
    title: `Title of ${taskId}`,
    status: "open",
    bucket: "open",
    dependsOn: [],
    unblocks: [],
    ...overrides,
  };
}

describe("matchesBoardQuery / filterBoard", () => {
  it("matches case-insensitively over id and title, preserving order", () => {
    const tasks = [task("alpha-1"), task("beta-2", { title: "The ALPHA follow-up" }), task("gama-3")];
    expect(filterBoard(tasks, { text: "ALPHA", buckets: null }).map((entry) => entry.taskId)).toEqual([
      "alpha-1",
      "beta-2",
    ]);
    expect(filterBoard(tasks, OPEN_BOARD_QUERY)).toHaveLength(3);
    expect(matchesBoardQuery(tasks[0] as BoardTask, { text: "  ", buckets: null })).toBe(true);
  });

  it("slices by bucket and composes bucket with text", () => {
    const tasks = [task("a", { bucket: "blocked" }), task("b", { bucket: "done" }), task("ab", { bucket: "done" })];
    expect(filterBoard(tasks, { text: "", buckets: ["done"] })).toHaveLength(2);
    expect(filterBoard(tasks, { text: "a", buckets: ["done"] }).map((entry) => entry.taskId)).toEqual(["ab"]);
  });
});

describe("boardQueryConstrained", () => {
  it("is false only for the open query", () => {
    expect(boardQueryConstrained(OPEN_BOARD_QUERY)).toBe(false);
    expect(boardQueryConstrained({ text: "x", buckets: null })).toBe(true);
    expect(boardQueryConstrained({ text: "", buckets: ["open"] })).toBe(true);
  });
});

describe("bucketCounts", () => {
  it("counts the unfiltered board per bucket", () => {
    expect(
      bucketCounts([task("a", { bucket: "blocked" }), task("b", { bucket: "blocked" }), task("c", { bucket: "done" })]),
    ).toEqual({ blocked: 2, ready: 0, open: 0, done: 1 });
  });
});

describe("toggleBucket", () => {
  it("walks null → single → pair → back to null", () => {
    const one = toggleBucket(OPEN_BOARD_QUERY, "blocked");
    expect(one.buckets).toEqual(["blocked"]);
    const two = toggleBucket(one, "done");
    expect(two.buckets).toEqual(["blocked", "done"]);
    const backToOne = toggleBucket(two, "done");
    expect(backToOne.buckets).toEqual(["blocked"]);
    expect(toggleBucket(backToOne, "blocked").buckets).toBeNull();
  });
});
