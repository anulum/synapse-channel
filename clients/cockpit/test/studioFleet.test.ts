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

it("keeps an opened focused task across changed exports and page shifts", () => {
  const panel = mount();
  panel.render(snapshot());
  const details = document.querySelector("details")!;
  details.open = true;
  document.querySelector("summary")!.focus();
  const updated = snapshot(51);
  updated.exported_at = 20;
  updated.snapshot.tasks[0]!.status = "done";
  panel.render(updated);
  expect(document.activeElement?.tagName).toBe("SUMMARY");
  expect(document.querySelector("details")?.open).toBe(true);
  expect(document.querySelector("details")?.textContent).toContain('"status": "done"');
  expect(document.body.textContent).toContain("Rows 51–52 of 52");
});

it.each(["removed", "source", "invalid"])("moves missing selection to panel status: %s", (change) => {
  const panel = mount();
  panel.render(snapshot());
  document.querySelector("details")!.open = true;
  document.querySelector("summary")!.focus();
  const updated = snapshot();
  if (change === "removed") updated.snapshot.tasks = [];
  if (change === "source") updated.source_id = "another-lab";
  panel.render(change === "invalid" ? {} : updated);
  expect(document.activeElement?.id).toBe("cc-fleet-status");
  expect(document.querySelector("details")?.open ?? false).toBe(false);
});

it("keeps keyboard pagination focus on an enabled control", () => {
  const panel = mount();
  panel.render(snapshot(51));
  const next = document.querySelectorAll<HTMLButtonElement>("button")[1]!;
  next.focus();
  next.click();
  expect(document.activeElement?.textContent).toBe("Previous mirror rows");
  (document.activeElement as HTMLButtonElement).click();
  expect(document.activeElement?.textContent).toBe("Next mirror rows");
});

it("does not steal focus from outside the panel during refresh", () => {
  const panel = mount();
  panel.render(snapshot());
  const outside = document.createElement("button");
  document.body.append(outside);
  outside.focus();
  panel.render(snapshot(2));
  expect(document.activeElement).toBe(outside);
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

it.each(["response", "body", "http error", "network error", "body error"])(
  "ignores superseded %s completion after a newer export", async (phase) => {
    const panel = mount();
    const old = deferred<unknown>();
    const body = deferred<unknown>();
    const newer = snapshot(2);
    newer.exported_at = 20;
    const fetcher = vi.fn().mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce({ok: true, json: async () => newer});
    vi.stubGlobal("fetch", fetcher);
    const first = panel.refresh();
    if (phase.startsWith("body")) {
      old.resolve({ok: true, json: () => body.promise});
      await Promise.resolve();
    }
    await panel.refresh();
    expect(document.body.textContent).toContain("exported at 20");
    if (phase === "network error") old.reject(new Error("connection lost"));
    else if (phase === "http error") old.resolve({ok: false, status: 503});
    else if (phase === "body error") body.reject(new Error("truncated JSON"));
    else if (phase === "body") body.resolve(snapshot());
    else old.resolve({ok: true, json: async () => snapshot()});
    await first;
    expect(document.body.textContent).toContain("exported at 20");
    expect(document.body.textContent).toContain("peer-1");
    expect(fetcher.mock.calls[0]![1].signal.aborted).toBe(true);
  },
);

it("does not restore an old success after access is revoked and can recover", async () => {
  const panel = mount();
  const body = deferred<unknown>();
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce({ok: true, json: () => body.promise})
    .mockResolvedValueOnce({ok: false, status: 403})
    .mockResolvedValueOnce({ok: true, json: async () => snapshot(2)}));
  const first = panel.refresh();
  await Promise.resolve();
  await panel.refresh();
  body.resolve(snapshot());
  await first;
  expect(document.getElementById("cc-fleet-status")?.textContent).toBe("locked");
  expect(document.getElementById("cc-fleet-mirror")?.textContent).toBe("");
  await panel.refresh();
  expect(document.body.textContent).toContain("peer-1");
});

it("clears on the active request deadline and recovers on the next refresh", async () => {
  const panel = mount();
  panel.render(snapshot());
  vi.stubGlobal("fetch", vi.fn((_url, options: RequestInit) => new Promise((_resolve, reject) => {
    options.signal!.addEventListener("abort", () => reject(new DOMException("timeout", "AbortError")));
  })));
  const pending = panel.refresh();
  await vi.advanceTimersByTimeAsync(5000);
  await pending;
  expect(document.getElementById("cc-fleet-status")?.textContent).toBe("mirror unavailable");
  expect(document.getElementById("cc-fleet-mirror")?.textContent).toBe("");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ok: true, json: async () => snapshot()}));
  await panel.refresh();
  expect(document.body.textContent).toContain("exported at 12");
});
