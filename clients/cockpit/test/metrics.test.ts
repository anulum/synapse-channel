// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — log-pulse metrics parsing and feed tests

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createMetricsStore,
  orderKindCounts,
  parseMetrics,
  type MetricsState,
} from "../src/lib/metrics";

afterEach(() => {
  vi.useRealTimers();
});

describe("parseMetrics", () => {
  it("parses the served document verbatim", () => {
    const metrics = parseMetrics({
      source: "event-store",
      log: { total_events: 5651, max_seq: 5651, first_ts: 1782.5, last_ts: 1783.9 },
      events_by_kind: { chat: 2800, claim: 1400 },
      windows: {
        last_hour: { events: 120, by_kind: { chat: 80, claim: 40 } },
        last_day: { events: 900, by_kind: { chat: 600, claim: 300 } },
      },
      note: "log metrics measured against the log's final timestamp",
    });
    expect(metrics).toEqual({
      source: "event-store",
      log: { totalEvents: 5651, maxSeq: 5651, firstTs: 1782.5, lastTs: 1783.9 },
      eventsByKind: { chat: 2800, claim: 1400 },
      windows: {
        last_hour: { events: 120, byKind: { chat: 80, claim: 40 } },
        last_day: { events: 900, byKind: { chat: 600, claim: 300 } },
      },
      note: "log metrics measured against the log's final timestamp",
    });
  });

  it("rejects non-objects and defaults every missing field safely", () => {
    expect(parseMetrics(null)).toBeNull();
    expect(parseMetrics([1])).toBeNull();
    const empty = parseMetrics({ log: "junk", events_by_kind: { chat: "junk" }, windows: { w: "junk" } });
    expect(empty).toEqual({
      source: "",
      log: { totalEvents: 0, maxSeq: 0, firstTs: null, lastTs: null },
      eventsByKind: { chat: 0 },
      windows: { w: { events: 0, byKind: {} } },
      note: "",
    });
  });
});

describe("orderKindCounts", () => {
  it("orders by count desc, ties by kind name", () => {
    expect(orderKindCounts({ release: 5, chat: 9, claim: 5 })).toEqual([
      ["chat", 9],
      ["claim", 5],
      ["release", 5],
    ]);
    expect(orderKindCounts({})).toEqual([]);
  });
});

describe("createMetricsStore", () => {
  it("rides the shared feed lifecycle: absent then live", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("no", { status: 404 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ source: "event-store", log: {}, events_by_kind: {}, windows: {}, note: "n" })),
      );
    const states: MetricsState[] = [];
    const store = createMetricsStore({ fetcher, pollMs: 1000, now: () => 9_000 });
    store.subscribe((state) => states.push(state));
    await vi.waitFor(() => {
      expect(states).toContainEqual(expect.objectContaining({ status: "absent" }));
    });
    await vi.advanceTimersByTimeAsync(1000);
    await vi.waitFor(() => {
      expect(states.at(-1)?.status).toBe("live");
    });
    expect(states.at(-1)?.data?.note).toBe("n");
    expect(states.at(-1)?.fetchedAt).toBe(9_000);
    store.stop();
  });

  it("runs on its defaults and surfaces the relative-URL failure", async () => {
    vi.useFakeTimers();
    const states: MetricsState[] = [];
    const store = createMetricsStore();
    store.subscribe((state) => states.push(state));
    await vi.waitFor(() => {
      expect(states.at(-1)?.status).toBe("error");
    });
    store.stop();
  });
});
