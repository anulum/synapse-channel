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

function loadCommand(feedStart: ReturnType<typeof vi.fn>): StudioCommand {
  document.body.insertAdjacentHTML(
    "beforeend",
    '<script id="syn-studio-config" type="application/json">' +
      '{"snapshotUrl":"/studio.json","pollMs":5000}</script>',
  );
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({ matches: true })),
  });
  Object.assign(window, { SynapseStudioFeeds: { start: feedStart } });
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => undefined)));
  window.eval(boardSource);
  window.eval(commandSource);
  return (window as typeof window & { SynapseStudioCommand: StudioCommand }).SynapseStudioCommand;
}

afterEach(() => {
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
    agents: { live: [payload], missing_waiters: [] },
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

it("shows a precise offline state when snapshot polling fails", async () => {
  mount();
  const feedStart = vi.fn();
  const command = loadCommand(feedStart);
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("connection refused")));
  vi.spyOn(globalThis, "setTimeout").mockImplementation(() => 1 as never);

  command.start();

  await vi.waitFor(() => {
    expect(document.getElementById("cc-offline")?.textContent).toContain("connection refused");
  });
  expect(document.getElementById("cc-offline")?.hidden).toBe(false);
  expect(document.getElementById("cc-connection")?.textContent).toBe("offline");
});
