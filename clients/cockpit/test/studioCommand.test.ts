// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — Studio command asset snapshot and DOM security regressions

// @vitest-environment jsdom

/// <reference types="node" />

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, expect, it, vi } from "vitest";

type StudioCommand = {
  render(data: Record<string, unknown>): void;
  start(): void;
};

const boardSource = readFileSync(
  resolve(process.cwd(), "../../src/synapse_channel/dashboard_assets/board-columns.js"),
  "utf8",
);
const commandSource = readFileSync(
  resolve(process.cwd(), "../../src/synapse_channel/dashboard_assets/studio-command.js"),
  "utf8",
);

function mount(): void {
  const ids = [
    "cc-offline", "cc-connection", "cc-hub", "cc-version", "cc-verdict", "cc-agents",
    "cc-claims", "cc-tasks", "cc-conflicts", "cc-signals", "cc-posture", "cc-peers",
    "cc-agents-list", "cc-claims-list", "cc-tasks-list", "cc-board-columns", "cc-risk-list",
    "cc-posture-list", "cc-peers-list", "cc-fallback-body",
  ];
  document.body.innerHTML = ids.map((id) => {
    if (id === "cc-fallback-body") return `<table><tbody id="${id}"></tbody></table>`;
    if (id === "cc-offline") return `<span id="${id}" hidden></span>`;
    return `<div id="${id}"></div>`;
  }).join("") + '<svg id="cc-clock"></svg>';
}

function loadCommand(feedStart: ReturnType<typeof vi.fn>, fetcher = vi.fn(() => new Promise<Response>(() => undefined))): StudioCommand {
  document.body.insertAdjacentHTML(
    "beforeend",
    '<script id="syn-studio-config" type="application/json">' +
      '{"snapshotUrl":"/studio.json","pollMs":5000,"snapshotTimeoutMs":7000}</script>',
  );
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({ matches: true })),
  });
  Object.assign(window, { SynapseStudioFeeds: { start: feedStart } });
  vi.stubGlobal("fetch", fetcher);
  window.eval(boardSource);
  window.eval(commandSource);
  return (window as typeof window & { SynapseStudioCommand: StudioCommand }).SynapseStudioCommand;
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("renders the snapshot, exact board projection, and untrusted fields as inert text", () => {
  mount();
  const feedStart = vi.fn();
  const command = loadCommand(feedStart);
  const payload = `<img id="pwn" onerror="window.__pwned=1">\"'`;

  command.render({
    verdict: "amber",
    hub: { id: payload, version: payload },
    headline: {
      agents_live: 1,
      claims_active: 1,
      tasks_ready: 1,
      risk_signals: 1,
      peers_total: 1,
      peers_reachable: 1,
    },
    agents: { live: [payload], missing_waiters: [], seats: [{ identity: payload, state: payload }] },
    claims: { active: [{ owner: payload, paths: [payload] }], stale: [] },
    tasks: {
      ready: [payload],
      blocked: [],
      columns: {
        columns: [{ id: "open", label: "Open", tasks: [{ task_id: payload, title: payload }] }],
      },
    },
    conflicts: [],
    risk: { signals: [{ level: "amber", subject: payload, detail: payload }] },
    security_posture: { level: "amber", rows: [{ level: "amber", surface: payload, state: payload }] },
    observed_fleet: { peers: [{ level: "amber", hub_id: payload, detail: payload }] },
  });

  expect(document.getElementById("cc-hub")?.textContent).toBe(payload);
  expect(document.getElementById("cc-board-columns")?.textContent).toContain(payload);
  expect(document.getElementById("cc-connection")?.textContent).toBe("connected");
  expect(document.querySelector("#pwn, img, [onerror]")).toBeNull();
  expect((window as typeof window & { __pwned?: number }).__pwned).toBeUndefined();
  expect(feedStart).toHaveBeenCalledWith(expect.objectContaining({ snapshotUrl: "/studio.json" }));
});

it("keeps keyboard focus on a panel's remainder when the next snapshot arrives", () => {
  mount();
  const command = loadCommand(vi.fn());
  const snapshot = {
    tasks: { ready: Array.from({ length: 12 }, (_unused, index) => `TASK-${index + 1}`), blocked: [] },
  };

  command.render(snapshot);
  document.querySelector<HTMLElement>("#cc-tasks-list summary")?.focus();
  command.render(snapshot);

  // The list is rebuilt; a keyboard user's next Enter must still reach its summary.
  expect(document.activeElement).toBe(document.querySelector("#cc-tasks-list summary"));
  command.render({ tasks: { ready: ["TASK-1"], blocked: [] } });
  expect(document.querySelector("#cc-tasks-list summary")).toBeNull();
});

it("lists each seat with its state and tone, never as offline", () => {
  mount();
  const command = loadCommand(vi.fn());

  command.render({
    agents: {
      live: [],
      missing_waiters: [],
      seats: [
        { identity: "seat/ear", state: "listening" },
        { identity: "seat/hand", state: "active, not listening" },
        { identity: "seat/quiet", state: "silent" },
        { identity: "seat/deaf", state: "listening", reaction: { proven: false, mark: "deaf ~5h" } },
      ],
    },
  });

  const rows = Array.from(document.querySelectorAll("#cc-agents-list > .syn-row"));
  expect(rows.map((row) => [row.querySelector(".syn-dot")?.className, row.textContent])).toEqual([
    ["syn-dot syn-dot--ok", "seat/earlistening"],
    ["syn-dot syn-dot--warn", "seat/handactive, not listening"],
    ["syn-dot syn-dot--dark", "seat/quietsilent"],
    // The hub's mark, as synapse who prints it, and a warning tone.
    ["syn-dot syn-dot--warn", "seat/deaflistening · deaf ~5h"],
  ]);
  expect(rows.map((row) => row.querySelector(".cc-muted")?.textContent)).toEqual([
    "listening",
    "active, not listening",
    "silent",
    "listening · deaf ~5h",
  ]);
});

it("shows a precise offline state when snapshot polling fails", async () => {
  mount();
  const feedStart = vi.fn();
  vi.spyOn(globalThis, "setTimeout").mockImplementation(() => 1 as never);
  loadCommand(feedStart, vi.fn().mockRejectedValue(new Error("connection refused")));

  await vi.waitFor(() => {
    expect(document.getElementById("cc-offline")?.textContent).toContain("connection refused");
  });
  expect(document.getElementById("cc-offline")?.hidden).toBe(false);
  expect(document.getElementById("cc-connection")?.textContent).toBe("stale");
});

it("starts one authenticated polling loop and one feed subscription", () => {
  vi.useFakeTimers();
  mount();
  vi.stubGlobal("SynapseStudioAccess", { authHeaders: () => ({ Authorization: "Bearer test-session" }) });
  const fetcher = vi.fn(() => new Promise<Response>(() => undefined));
  const feeds = vi.fn();
  const command = loadCommand(feeds, fetcher);
  command.start();
  command.start();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(feeds).toHaveBeenCalledTimes(1);
  expect(fetcher).toHaveBeenCalledWith("/studio.json", expect.objectContaining({
    headers: { Authorization: "Bearer test-session" },
    signal: expect.any(AbortSignal),
  }));
});

it("marks retained data non-current after timeout and resumes polling", async () => {
  vi.useFakeTimers();
  mount();
  const fetcher = vi.fn().mockImplementationOnce((_url, options: RequestInit) =>
    new Promise((_resolve, reject) => options.signal!.addEventListener("abort", () =>
      reject(new DOMException("deadline", "AbortError")))))
    .mockResolvedValueOnce(new Response(JSON.stringify({ hub: { id: "recovered" } })));
  const command = loadCommand(vi.fn(), fetcher);
  command.render({ hub: { id: "previous" } });
  await vi.advanceTimersByTimeAsync(5000);
  expect(document.getElementById("cc-connection")?.textContent).toBe("connected");
  await vi.advanceTimersByTimeAsync(2000);
  expect(document.getElementById("cc-connection")?.textContent).toBe("stale");
  expect(document.getElementById("cc-offline")?.textContent).toContain("refresh timed out");
  expect(document.getElementById("cc-offline")?.textContent).toContain("not current");
  expect(document.getElementById("cc-hub")?.textContent).toBe("previous");
  await vi.advanceTimersByTimeAsync(5000);
  expect(document.getElementById("cc-connection")?.textContent).toBe("connected");
  expect(document.getElementById("cc-hub")?.textContent).toBe("recovered");
  expect(document.getElementById("cc-offline")?.hidden).toBe(true);
});

it("bounds the panel lists and the board, stating each remainder and keeping every row", () => {
  mount();
  const command = loadCommand(vi.fn());
  const ids = Array.from({ length: 30 }, (_unused, index) => `TASK-${index + 1}`);

  command.render({
    verdict: "amber",
    headline: { agents_live: 30, tasks_ready: 30 },
    agents: {
      live: ids.map((id) => `seat/${id}`),
      missing_waiters: [],
      seats: ids.map((id) => ({ identity: `seat/${id}`, state: "listening" })),
    },
    claims: { active: [], stale: [] },
    tasks: {
      ready: ids,
      blocked: [],
      columns: {
        columns: [
          { id: "open", label: "Open", tasks: ids.map((id) => ({ task_id: id, title: id })) },
          { id: "closed", label: "Closed", tasks: ids.slice(0, 5).map((id) => ({ task_id: `done-${id}`, title: id })) },
        ],
      },
    },
    risk: { signals: [{ level: "amber", subject: "one", detail: "only" }] },
  });

  const tasks = document.getElementById("cc-tasks-list");
  expect(tasks?.querySelectorAll(":scope > .syn-row")).toHaveLength(8);
  expect(tasks?.querySelector(":scope > details.cc-more > summary")?.textContent).toBe("22 more of 30");
  expect(tasks?.querySelectorAll(".syn-row")).toHaveLength(30);
  expect(tasks?.textContent).toContain("TASK-30");

  const agents = document.getElementById("cc-agents-list");
  expect(agents?.querySelectorAll(":scope > .syn-row")).toHaveLength(8);
  expect(agents?.querySelector("details.cc-more > summary")?.textContent).toBe("22 more of 30");

  const risk = document.getElementById("cc-risk-list");
  expect(risk?.querySelectorAll(":scope > .syn-row")).toHaveLength(1);
  expect(risk?.querySelector("details")).toBeNull();

  const board = document.getElementById("cc-board-columns");
  const open = board?.querySelector('[data-column="open"].syn-board-column');
  expect(open?.querySelector(".syn-board-column__count")?.textContent).toBe("30");
  expect(open?.querySelectorAll(".syn-board-column__body > .syn-board-card")).toHaveLength(6);
  expect(open?.querySelector("details.syn-board-more > summary")?.textContent).toBe("24 more of 30");
  const closed = board?.querySelector('[data-column="closed"].syn-board-column');
  expect(closed?.querySelectorAll(".syn-board-column__body > .syn-board-card")).toHaveLength(0);
  expect(closed?.querySelector("details.syn-board-more > summary")?.textContent).toBe("5 closed");
  expect(document.querySelector("button,input,form")).toBeNull();
});

it("keeps an opened panel list open when the next snapshot arrives", () => {
  mount();
  const command = loadCommand(vi.fn());
  const snapshot = {
    tasks: { ready: Array.from({ length: 12 }, (_unused, index) => `TASK-${index + 1}`), blocked: [] },
    agents: {
      live: Array.from({ length: 12 }, (_unused, index) => `seat/${index + 1}`),
      missing_waiters: [],
      seats: Array.from({ length: 12 }, (_unused, index) => ({ identity: `seat/${index + 1}`, state: "silent" })),
    },
  };

  command.render(snapshot);
  const first = document.querySelector<HTMLDetailsElement>("#cc-tasks-list details.cc-more");
  expect(first?.open).toBe(false);
  first!.open = true;
  first!.dispatchEvent(new Event("toggle"));

  command.render(snapshot);
  expect(document.querySelector<HTMLDetailsElement>("#cc-tasks-list details.cc-more")?.open).toBe(true);
  expect(document.querySelector<HTMLDetailsElement>("#cc-agents-list details.cc-more")?.open).toBe(false);
});
