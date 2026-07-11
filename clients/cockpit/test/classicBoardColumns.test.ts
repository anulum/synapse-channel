// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — board-column asset DOM and injection regressions

// @vitest-environment jsdom

/// <reference types="node" />

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, it } from "vitest";

type BoardColumns = {
  render(host: HTMLElement | null, projection: unknown): void;
};

const boardSource = readFileSync(
  resolve(process.cwd(), "../../src/synapse_channel/dashboard_assets/board-columns.js"),
  "utf8",
);

function loadBoardColumns(): BoardColumns {
  window.eval(boardSource);
  return (window as typeof window & { SynapseBoardColumns: BoardColumns }).SynapseBoardColumns;
}

it("renders fixed columns, exact statuses, flags, and truncation evidence", () => {
  document.body.innerHTML = '<div id="board"></div>';
  const board = loadBoardColumns();
  const host = document.getElementById("board");

  board.render(host, {
    columns: [
      {
        id: "working",
        label: "Working",
        tasks: [
          {
            task_id: "TASK-2",
            title: "Run verification",
            column: "working",
            board_status: "in_progress",
            claim_status: "working",
            ready: true,
            declared: false,
            lease_stale: true,
            owner: "seat/two",
            blocked_by: ["TASK-1"],
            unknown_dependencies: ["TASK-HIDDEN"],
          },
        ],
      },
      { id: "closed", label: "Closed", tasks: [] },
    ],
    source_truncated: true,
    declared_tasks: 2,
    total_declared_tasks: 9,
  });

  expect(host?.querySelectorAll(".syn-board-column")).toHaveLength(2);
  expect(host?.querySelector(".syn-board-card__status")?.textContent).toBe(
    "in_progress · working",
  );
  expect(host?.textContent).toContain("ready");
  expect(host?.textContent).toContain("ad-hoc claim");
  expect(host?.textContent).toContain("stale lease");
  expect(host?.textContent).toContain("waiting onTASK-1");
  expect(host?.textContent).toContain("dependency state unknownTASK-HIDDEN");
  expect(host?.textContent).toContain("Source snapshot truncated: showing 2 of 9");
  expect(host?.querySelector('[data-column="working"]')).not.toBeNull();
});

it("keeps every displayed field inert under quote, tag, and event payloads", () => {
  document.body.innerHTML = '<div id="board"></div>';
  const board = loadBoardColumns();
  const host = document.getElementById("board");
  const payload = `x\"><img id="pwn" onerror="window.__pwned=1"><button onclick='x'>`;

  board.render(host, {
    columns: [
      {
        id: payload,
        label: payload,
        tasks: [
          {
            task_id: payload,
            title: payload,
            column: payload,
            board_status: payload,
            claim_status: payload,
            owner: payload,
            suggested_owner: payload,
            blocked_by: [payload],
            unknown_dependencies: [payload],
          },
        ],
      },
    ],
  });

  expect(host?.textContent).toContain(payload);
  expect(host?.querySelector("#pwn")).toBeNull();
  expect(host?.querySelector("img,button,[onerror],[onclick]")).toBeNull();
  expect((window as typeof window & { __pwned?: number }).__pwned).toBeUndefined();
  expect(host?.querySelector(".syn-board-card")?.getAttribute("aria-label")).toContain(payload);
});

it("fails visibly for absent columns and ignores malformed rows", () => {
  document.body.innerHTML = '<div id="board"></div>';
  const board = loadBoardColumns();
  const host = document.getElementById("board");

  board.render(host, { columns: "not-a-list" });
  expect(host?.textContent).toBe("Board projection unavailable");

  board.render(host, { columns: [null, "bad", { id: "open", tasks: [null, "bad"] }] });
  expect(host?.querySelectorAll(".syn-board-column")).toHaveLength(1);
  expect(host?.textContent).toContain("No tasks");
  expect(() => board.render(null, {})).not.toThrow();
});
