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
  render(host: HTMLElement | null, projection: unknown, options?: unknown): void;
};

function cards(count: number, prefix: string): { task_id: string; title: string }[] {
  return Array.from({ length: count }, (_unused, index) => ({
    task_id: `${prefix}-${index + 1}`,
    title: `${prefix} task ${index + 1}`,
  }));
}

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

it("bounds each column to the card limit, states the remainder, and keeps every count whole", () => {
  document.body.innerHTML = '<div id="board"></div>';
  const board = loadBoardColumns();
  const host = document.getElementById("board");

  board.render(
    host,
    {
      columns: [
        { id: "open", label: "Open", tasks: cards(5, "OPEN") },
        { id: "working", label: "Working", tasks: cards(2, "WORK") },
        { id: "closed", label: "Closed", tasks: cards(3, "DONE") },
      ],
    },
    { cardLimit: 2, collapsed: ["closed"] },
  );

  const open = host?.querySelector('[data-column="open"].syn-board-column');
  expect(open?.querySelector(".syn-board-column__count")?.textContent).toBe("5");
  expect(open?.querySelectorAll(".syn-board-column__body > .syn-board-card")).toHaveLength(2);
  const more = open?.querySelector("details.syn-board-more");
  expect(more?.querySelector("summary")?.textContent).toBe("3 more of 5");
  expect(more?.hasAttribute("open")).toBe(false);
  expect(more?.querySelectorAll(".syn-board-card")).toHaveLength(3);
  expect(more?.textContent).toContain("OPEN-5");

  const working = host?.querySelector('[data-column="working"].syn-board-column');
  expect(working?.querySelectorAll(".syn-board-column__body > .syn-board-card")).toHaveLength(2);
  expect(working?.querySelector("details")).toBeNull();

  const closed = host?.querySelector('[data-column="closed"].syn-board-column');
  expect(closed?.querySelector(".syn-board-column__count")?.textContent).toBe("3");
  expect(closed?.querySelectorAll(".syn-board-column__body > .syn-board-card")).toHaveLength(0);
  expect(closed?.querySelector("details.syn-board-more > summary")?.textContent).toBe("3 closed");
  expect(closed?.querySelectorAll("details .syn-board-card")).toHaveLength(3);

  expect(host?.querySelectorAll(".syn-board-card")).toHaveLength(10);
  expect(host?.querySelector("button,input,form")).toBeNull();
});

it("keeps a disclosure the reader opened across a refresh, per column", () => {
  document.body.innerHTML = '<div id="board"></div>';
  const board = loadBoardColumns();
  const host = document.getElementById("board");
  const projection = {
    columns: [
      { id: "open", label: "Open", tasks: cards(4, "OPEN") },
      { id: "blocked", label: "Blocked", tasks: cards(4, "BLOCK") },
    ],
  };
  const options = { cardLimit: 1 };

  board.render(host, projection, options);
  const first = host?.querySelector<HTMLDetailsElement>('[data-column="open"] details');
  expect(first).not.toBeNull();
  first!.open = true;
  first!.dispatchEvent(new Event("toggle"));

  board.render(host, projection, options);
  expect(host?.querySelector<HTMLDetailsElement>('[data-column="open"] details')?.open).toBe(true);
  expect(host?.querySelector<HTMLDetailsElement>('[data-column="blocked"] details')?.open).toBe(false);

  const reopened = host?.querySelector<HTMLDetailsElement>('[data-column="open"] details');
  reopened!.open = false;
  reopened!.dispatchEvent(new Event("toggle"));
  board.render(host, projection, options);
  expect(host?.querySelector<HTMLDetailsElement>('[data-column="open"] details')?.open).toBe(false);
});

it("keeps keyboard focus on the column remainder it was on across a refresh", () => {
  document.body.innerHTML = '<div id="board"></div>';
  const board = loadBoardColumns();
  const host = document.getElementById("board");
  const projection = {
    columns: [
      { id: "open", label: "Open", tasks: cards(4, "OPEN") },
      { id: "blocked", label: "Blocked", tasks: cards(4, "BLOCK") },
    ],
  };
  const options = { cardLimit: 1 };
  const summaryOf = (column: string) =>
    host?.querySelector<HTMLElement>(`[data-column="${column}"] details > summary`);

  board.render(host, projection, options);
  summaryOf("blocked")?.focus();
  board.render(host, projection, options);
  expect(document.activeElement).toBe(summaryOf("blocked"));

  // A remainder that is gone takes no focus; focus elsewhere is never moved.
  board.render(host, { columns: [{ id: "open", label: "Open", tasks: cards(4, "OPEN") }] }, options);
  expect(document.activeElement).toBe(document.body);
  summaryOf("open")?.blur();
  board.render(host, projection, options);
  expect(document.activeElement).toBe(document.body);
});

it("renders every card when no usable limit is given", () => {
  document.body.innerHTML = '<div id="board"></div>';
  const board = loadBoardColumns();
  const host = document.getElementById("board");
  const projection = { columns: [{ id: "open", label: "Open", tasks: cards(4, "OPEN") }] };

  for (const options of [undefined, null, {}, { cardLimit: 0 }, { cardLimit: -2 }, { cardLimit: 1.5 },
    { cardLimit: Number.NaN }, { cardLimit: "3" }, { collapsed: "closed" }]) {
    board.render(host, projection, options);
    expect(host?.querySelectorAll(".syn-board-column__body > .syn-board-card")).toHaveLength(4);
    expect(host?.querySelector("details")).toBeNull();
  }
});
