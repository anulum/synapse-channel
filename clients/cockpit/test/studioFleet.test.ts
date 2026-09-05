// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — Fleet mirror panel DOM regressions
// @vitest-environment jsdom
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, expect, it, vi } from "vitest";

const source = readFileSync(resolve(process.cwd(),
  "../../src/synapse_channel/dashboard_assets/studio-fleet.js"), "utf8");
type Panel = { render(value: unknown): void; refresh(): Promise<void> };
function mount(): Panel {
  document.body.innerHTML = '<div id="cc-fleet-status"></div><div id="cc-fleet-mirror"></div>';
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
  new Function(source)();
  return (window as unknown as { SynapseStudioFleet: Panel }).SynapseStudioFleet;
}
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); document.body.replaceChildren(); });
function snapshot(count = 1) {
  return {version: 1, source_id: "lab", exported_at: 12, advisory: true, snapshot: {
    generated_at: 11, peers: Array.from({length: count}, (_, i) => ({
      peer_id: "peer-" + i, cursor: 1, events: 1, status_written_at: null,
      last_success_at: null, consecutive_failures: null, caught_up: false,
      budget_exhausted_reason: "pages",
    })), tasks: [{task_id: "<img onerror=alert(1)>", status: "open",
      board_conflict: {contenders: [{hub_id: "other"}]}}],
  }};
}
it("preserves unknown evidence and expands conflict text without HTML interpretation", () => {
  const panel = mount();
  panel.render(snapshot());
  expect(document.body.textContent).toContain("failures unknown");
  expect(document.body.textContent).toContain("caught up false");
  expect(document.body.textContent).toContain("unresolved conflict");
  const details = document.querySelector("details")!;
  details.open = true;
  details.dispatchEvent(new Event("toggle"));
  expect(details.textContent).toContain("contenders");
  expect(document.querySelector("img")).toBeNull();
});
it("paginates without dropping rows or creating action controls", () => {
  const panel = mount();
  panel.render(snapshot(51));
  expect(document.body.textContent).toContain("Rows 1–50 of 52");
  const next = Array.from(document.querySelectorAll("button")).find(x => x.textContent?.startsWith("Next"))!;
  next.click();
  expect(document.body.textContent).toContain("peer-50");
  expect(document.body.textContent).toContain("Rows 51–52 of 52");
  document.querySelector<HTMLButtonElement>("button")!.click();
  expect(document.body.textContent).toContain("Rows 1–50 of 52");
});
it.each([401, 403, 404, 409, 503, 500])("clears old data when HTTP %i replaces access", async (status) => {
  const panel = mount();
  panel.render(snapshot());
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ok: false, status}));
  await panel.refresh();
  expect(document.getElementById("cc-fleet-mirror")?.textContent).toBe("");
  expect(document.getElementById("cc-fleet-status")?.textContent).not.toContain("exported at");
});
it("keeps focus and disclosure for an unchanged export and rejects incompatible input", async () => {
  const panel = mount();
  const doc = snapshot();
  panel.render(doc);
  const details = document.querySelector("details");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ok: true, json: async () => doc}));
  await panel.refresh();
  expect(document.querySelector("details")).toBe(details);
  panel.render({version: 2});
  expect(document.body.textContent).toContain("incompatible");
});
