// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — Studio feed asset DOM and polling regressions

// @vitest-environment jsdom

/// <reference types="node" />

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, expect, it, vi } from "vitest";

type StudioFeeds = {
  renderEvents(documentJson: unknown): void;
  renderOperatorActions(documentJson: unknown): void;
  start(config: Record<string, unknown>): void;
};

const feedsSource = readFileSync(
  resolve(process.cwd(), "../../src/synapse_channel/dashboard_assets/studio-feeds.js"),
  "utf8",
);

function loadFeeds(): StudioFeeds {
  window.eval(feedsSource);
  return (window as typeof window & { SynapseStudioFeeds: StudioFeeds }).SynapseStudioFeeds;
}

function mount(): void {
  document.body.innerHTML = '<div id="cc-livefeed-list"></div><div id="cc-actions-list"></div>';
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("renders bounded event and action rows as inert text", () => {
  mount();
  const feeds = loadFeeds();
  const payload = `<img id="pwn" onerror="window.__pwned=1">\"'`;
  const events = Array.from({ length: 25 }, (_, index) => ({
    seq: index + 1,
    kind: payload,
    payload: { task_id: payload },
  }));

  feeds.renderEvents({ events, next_cursor: 25 });
  feeds.renderOperatorActions({
    present: true,
    actions: [{ seq: 8, direction: payload, action: payload, task_id: payload, status: payload }],
  });

  expect(document.querySelectorAll("#cc-livefeed-list .cc-feed-row")).toHaveLength(20);
  expect(document.querySelector("#cc-livefeed-list")?.textContent).toContain(payload);
  expect(document.querySelector("#cc-actions-list")?.textContent).toContain(payload);
  expect(document.querySelector("#pwn, img, [onerror]")).toBeNull();
  expect((window as typeof window & { __pwned?: number }).__pwned).toBeUndefined();

  feeds.renderEvents({ events: [] });
  expect(document.querySelectorAll("#cc-livefeed-list .cc-feed-row")).toHaveLength(20);
  feeds.renderOperatorActions({ present: true, actions: [] });
  expect(document.querySelector("#cc-actions-list")?.textContent).toBe(
    "no operator relay actions yet",
  );
  feeds.renderOperatorActions({ present: false, actions: "bad" });
  expect(document.querySelector("#cc-actions-list")?.textContent).toBe(
    "operator-actions feed not configured",
  );
});

it("polls both configured feeds, advances the cursor, and renders live results", async () => {
  mount();
  const feeds = loadFeeds();
  const timer = vi.spyOn(globalThis, "setTimeout").mockImplementation(() => 1 as never);
  const fetchMock = vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
    const url = String(input);
    if (url.startsWith("/events")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ next_cursor: 7, events: [{ seq: 7, kind: "claim", payload: {} }] }),
      } as Response;
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({ present: true, actions: [{ seq: 9, action: "task" }] }),
    } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);

  feeds.start({ eventsUrl: "/events", operatorActionsUrl: "/actions", pollMs: 25 });

  await vi.waitFor(() => {
    expect(document.querySelector("#cc-livefeed-list")?.textContent).toContain("#7claim");
    expect(document.querySelector("#cc-actions-list")?.textContent).toContain("#9relaytask");
  });
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(String(fetchMock.mock.calls[0]?.[0])).toContain("since=latest");
  expect(timer.mock.calls.filter((call) => call[1] === 1000)).toHaveLength(2);
});

it("reports absent and failed feeds without retrying an absent endpoint", async () => {
  mount();
  const feeds = loadFeeds();
  const timer = vi.spyOn(globalThis, "setTimeout").mockImplementation(() => 1 as never);
  const fetchMock = vi
    .fn<(input: RequestInfo | URL) => Promise<Response>>()
    .mockResolvedValueOnce({ ok: false, status: 404 } as Response)
    .mockRejectedValueOnce(new Error("offline"));
  vi.stubGlobal("fetch", fetchMock);

  feeds.start({ pollMs: 5000 });

  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(document.querySelector("#cc-livefeed-list")?.textContent).toBe("event feed not configured");
  expect(document.querySelector("#cc-actions-list")?.textContent).toBe(
    "operator-actions feed unavailable",
  );
  expect(timer.mock.calls.filter((call) => call[1] === 5000)).toHaveLength(1);
});
