// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — history-scrub fetch tests

import { describe, expect, it, vi } from "vitest";
import { fetchHistoryWindow, fetchLatestSeq } from "../src/lib/history";

function wire(seq: number): Record<string, unknown> {
  return { seq, ts: seq, kind: "chat", payload: { sender: "s", payload: `m${seq}` } };
}

function pageResponse(events: Record<string, unknown>[], nextCursor: number): Response {
  return new Response(JSON.stringify({ events, next_cursor: nextCursor }));
}

describe("fetchLatestSeq", () => {
  it("asks since=latest and returns the cursor", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(pageResponse([], 5612));
    expect(await fetchLatestSeq(fetcher)).toEqual({ kind: "loaded", latest: 5612 });
    expect(fetcher.mock.calls[0]?.[0]).toBe("/events.json?since=latest&limit=1");
  });

  it("maps 404, bad statuses, junk payloads, and thrown reasons", async () => {
    expect(
      await fetchLatestSeq(vi.fn<typeof fetch>().mockResolvedValue(new Response("no", { status: 404 }))),
    ).toEqual({ kind: "absent" });
    expect(
      await fetchLatestSeq(vi.fn<typeof fetch>().mockResolvedValue(new Response("boom", { status: 500 }))),
    ).toEqual({ kind: "error", message: "hub returned 500" });
    expect(
      await fetchLatestSeq(vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify([1])))),
    ).toEqual({ kind: "error", message: "events payload was not an object" });
    expect(
      await fetchLatestSeq(vi.fn<typeof fetch>().mockRejectedValue(new Error("torn down"))),
    ).toEqual({ kind: "error", message: "torn down" });
    expect(
      await fetchLatestSeq(vi.fn<typeof fetch>().mockRejectedValue("plain reason")),
    ).toEqual({ kind: "error", message: "plain reason" });
  });
});

describe("fetchHistoryWindow", () => {
  it("fetches the window ending at the position, newest first, position clamped in", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(pageResponse([wire(101), wire(102), wire(103)], 103));
    const result = await fetchHistoryWindow(103, 3, fetcher);
    expect(fetcher.mock.calls[0]?.[0]).toBe("/events.json?since=100&limit=3");
    expect(result.kind).toBe("loaded");
    if (result.kind === "loaded") {
      expect(result.window.events.map((event) => event.seq)).toEqual([103, 102, 101]);
      expect(result.window.fromSeq).toBe(101);
      expect(result.window.toSeq).toBe(103);
    }
  });

  it("drops events past the position (a race with a growing log) and clamps since at zero", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(pageResponse([wire(1), wire(2), wire(3)], 3));
    const result = await fetchHistoryWindow(2, 5, fetcher);
    expect(fetcher.mock.calls[0]?.[0]).toBe("/events.json?since=0&limit=5");
    if (result.kind === "loaded") {
      expect(result.window.events.map((event) => event.seq)).toEqual([2, 1]);
      expect(result.window.toSeq).toBe(2);
    }
  });

  it("answers an empty window honestly and maps failure shapes", async () => {
    const empty = await fetchHistoryWindow(50, 5, vi.fn<typeof fetch>().mockResolvedValue(pageResponse([], 50)));
    if (empty.kind === "loaded") {
      expect(empty.window.events).toEqual([]);
      expect(empty.window.fromSeq).toBe(0);
    }
    expect(
      await fetchHistoryWindow(1, 5, vi.fn<typeof fetch>().mockResolvedValue(new Response("no", { status: 404 }))),
    ).toEqual({ kind: "absent" });
    expect(
      await fetchHistoryWindow(1, 5, vi.fn<typeof fetch>().mockResolvedValue(new Response("boom", { status: 503 }))),
    ).toEqual({ kind: "error", message: "hub returned 503" });
    expect(
      await fetchHistoryWindow(1, 5, vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify([1])))),
    ).toEqual({ kind: "error", message: "events payload was not an object" });
    expect(
      await fetchHistoryWindow(1, 5, vi.fn<typeof fetch>().mockRejectedValue(new Error("gone"))),
    ).toEqual({ kind: "error", message: "gone" });
    expect(
      await fetchHistoryWindow(1, 5, vi.fn<typeof fetch>().mockRejectedValue("plain")),
    ).toEqual({ kind: "error", message: "plain" });
  });

  it("runs on its defaults against the global fetch, which fails visibly in tests", async () => {
    const viaDefaults = await fetchHistoryWindow(10);
    expect(viaDefaults.kind).toBe("error");
    const latestViaDefaults = await fetchLatestSeq();
    expect(latestViaDefaults.kind).toBe("error");
  });
});
