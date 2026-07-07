// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — hub-attested event-tail parsing, mapping, and polling tests

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createEventsTailSource,
  mapStoredEvent,
  parseStoredEvent,
  parseTail,
  type SpineProvenance,
  type StoredEvent,
} from "../src/lib/eventsTail";
import type { CockpitEvent } from "../src/types";

afterEach(() => {
  vi.useRealTimers();
});

function stored(seq: number, kind: string, payload: Record<string, unknown>): StoredEvent {
  return { seq, ts: seq * 10, kind, payload };
}

describe("parseStoredEvent / parseTail", () => {
  it("parses a wire event tolerantly and drops malformed entries", () => {
    expect(parseStoredEvent({ seq: 7, ts: 1.5, kind: "claim", payload: { a: 1 } })).toEqual({
      seq: 7,
      ts: 1.5,
      kind: "claim",
      payload: { a: 1 },
    });
    expect(parseStoredEvent("junk")).toEqual({ seq: 0, ts: 0, kind: "", payload: {} });
    const tail = parseTail({ events: [{ seq: 3, ts: 1, kind: "chat", payload: {} }, "junk"], next_cursor: 3 });
    expect(tail?.events).toHaveLength(1);
    expect(tail?.nextCursor).toBe(3);
    expect(parseTail({ events: "junk" })).toEqual({ events: [], nextCursor: 0 });
    expect(parseTail(null)).toBeNull();
    expect(parseTail([1])).toBeNull();
  });
});

describe("mapStoredEvent", () => {
  it("maps a dead-letter escalation into the risk lane, loud", () => {
    const event = mapStoredEvent({
      seq: 9001,
      ts: 1783.5,
      kind: "dead_letter_escalation",
      payload: { target: "CEO", count: 10, last_sender: "a/say", threshold: 5 },
    });
    expect(event.kind).toBe("conflict");
    expect(event.lane).toBe("risk");
    expect(event.actor).toBe("CEO");
    expect(event.label).toBe("dead-letter escalation: CEO · 10 undelivered");
    const bare = mapStoredEvent({ seq: 1, ts: 1, kind: "dead_letter_escalation", payload: { target: "x" } });
    expect(bare.label).toBe("dead-letter escalation: x");
  });

  it("maps a dead-letter forward as an audit finding, direction and hubs named", () => {
    const event = mapStoredEvent({
      seq: 9002,
      ts: 1784.5,
      kind: "dead_letter_forwarding",
      payload: { target: "CEO", count: 10, origin_hub_id: "hub-a", owner_hub_id: "hub-b", direction: "out" },
    });
    expect(event.kind).toBe("finding");
    expect(event.actor).toBe("CEO");
    expect(event.label).toBe("dead-letter forward (out): CEO · 10 undelivered · hub-a → hub-b");
    // A record missing the optional trimmings still states the core fact.
    const bare = mapStoredEvent({ seq: 1, ts: 1, kind: "dead_letter_forwarding", payload: { target: "x" } });
    expect(bare.label).toBe("dead-letter forward: x");
    const half = mapStoredEvent({
      seq: 2,
      ts: 2,
      kind: "dead_letter_forwarding",
      payload: { target: "y", count: "many", origin_hub_id: "hub-a", direction: "in" },
    });
    expect(half.label).toBe("dead-letter forward (in): y");
  });


  it("maps every known hub kind with the hub's own seq and ts", () => {
    expect(mapStoredEvent(stored(1, "claim", { task_id: "t1", owner: "a" }))).toMatchObject({
      seq: 1,
      ts: 10,
      kind: "claim",
      lane: "claims",
      actor: "a",
      taskId: "t1",
      label: "claimed t1",
    });
    expect(mapStoredEvent(stored(2, "release", { task_id: "t1" }))).toMatchObject({
      kind: "release",
      actor: "",
      label: "released t1",
    });
    expect(
      mapStoredEvent(stored(3, "ledger_progress", { task_id: "t1", author: "b", kind: "finding", text: "found" })),
    ).toMatchObject({ kind: "finding", actor: "b", label: "t1: found" });
    expect(
      mapStoredEvent(stored(4, "ledger_progress", { author: "b", kind: "note", text: "bare note" })),
    ).toMatchObject({ kind: "chat", label: "bare note", taskId: "" });
    expect(
      mapStoredEvent(stored(5, "ledger_task", { task_id: "t2", status: "open", created_by: "c" })),
    ).toMatchObject({ kind: "task", actor: "c", label: "task t2 (open)" });
    expect(mapStoredEvent(stored(6, "ledger_task", { task_id: "t3" }))).toMatchObject({
      label: "task t3",
    });
    expect(mapStoredEvent(stored(7, "chat", { sender: "d", payload: "hello fleet" }))).toMatchObject({
      kind: "chat",
      actor: "d",
      label: "hello fleet",
    });
  });

  it("truncates an over-long chat payload and shows unknown kinds by name", () => {
    const long = "x".repeat(400);
    const chat = mapStoredEvent(stored(8, "chat", { sender: "d", payload: long }));
    expect(chat.label.length).toBeLessThan(200);
    expect(chat.label.endsWith("…")).toBe(true);
    expect(mapStoredEvent(stored(9, "checkpoint", {}))).toMatchObject({
      kind: "chat",
      label: "checkpoint",
      actor: "",
    });
  });
});

interface Page {
  readonly events: { seq: number; ts: number; kind: string; payload: Record<string, unknown> }[];
  readonly next_cursor: number;
}

function pageResponse(page: Page): Response {
  return new Response(JSON.stringify(page));
}

function wire(seq: number): Page["events"][number] {
  return { seq, ts: seq, kind: "chat", payload: { sender: "s", payload: `m${seq}` } };
}

describe("createEventsTailSource", () => {
  it("finds the tail in two requests (latest + backfill), then polls forward", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(pageResponse({ events: [], next_cursor: 5 }))
      .mockResolvedValueOnce(pageResponse({ events: [wire(3), wire(4), wire(5)], next_cursor: 5 }))
      .mockResolvedValueOnce(pageResponse({ events: [wire(6)], next_cursor: 6 }));
    const events: CockpitEvent[] = [];
    const modes: SpineProvenance[] = [];
    const source = createEventsTailSource({ fetcher, pollMs: 1000, limit: 2, historyLimit: 3 });
    source.subscribe((event) => events.push(event));
    source.subscribeMode((mode) => modes.push(mode));

    await vi.waitFor(() => {
      expect(modes.at(-1)).toBe("hub");
    });
    expect(events.map((event) => event.seq)).toEqual([3, 4, 5]);
    expect(fetcher.mock.calls[0]?.[0]).toBe("/events.json?since=latest&limit=1");
    expect(fetcher.mock.calls[1]?.[0]).toBe("/events.json?since=2&limit=3");

    await vi.advanceTimersByTimeAsync(1000);
    await vi.waitFor(() => {
      expect(events.map((event) => event.seq)).toEqual([3, 4, 5, 6]);
    });
    expect(fetcher.mock.calls[2]?.[0]).toBe("/events.json?since=5&limit=2");
    expect(modes[0]).toBe("connecting");
    source.stop();
  });

  it("clamps the backfill start to zero on a short log and reports a backfill 404 as absent", async () => {
    vi.useFakeTimers();
    const short = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(pageResponse({ events: [], next_cursor: 2 }))
      .mockResolvedValueOnce(pageResponse({ events: [wire(1), wire(2)], next_cursor: 2 }));
    const shortModes: SpineProvenance[] = [];
    const shortSource = createEventsTailSource({ fetcher: short, pollMs: 1000, historyLimit: 10 });
    shortSource.subscribeMode((mode) => shortModes.push(mode));
    await vi.waitFor(() => {
      expect(shortModes.at(-1)).toBe("hub");
    });
    expect(short.mock.calls[1]?.[0]).toBe("/events.json?since=0&limit=10");
    shortSource.stop();

    const halfAbsent = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(pageResponse({ events: [], next_cursor: 5 }))
      .mockResolvedValueOnce(new Response("no", { status: 404 }));
    const halfModes: SpineProvenance[] = [];
    const halfSource = createEventsTailSource({ fetcher: halfAbsent, pollMs: 1000 });
    halfSource.subscribeMode((mode) => halfModes.push(mode));
    await vi.waitFor(() => {
      expect(halfModes.at(-1)).toBe("absent");
    });
    halfSource.stop();
  });

  it("reports absent on 404, re-checks slowly, and comes alive when served", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("no", { status: 404 }))
      .mockResolvedValueOnce(pageResponse({ events: [], next_cursor: 1 }))
      .mockResolvedValueOnce(pageResponse({ events: [wire(1)], next_cursor: 1 }));
    const modes: SpineProvenance[] = [];
    const source = createEventsTailSource({ fetcher, pollMs: 1000, absentPollMs: 5000 });
    source.subscribeMode((mode) => modes.push(mode));

    await vi.waitFor(() => {
      expect(modes).toContain("absent");
    });
    await vi.advanceTimersByTimeAsync(5000);
    await vi.waitFor(() => {
      expect(modes.at(-1)).toBe("hub");
    });
    expect(fetcher).toHaveBeenCalledTimes(3);
    source.stop();
  });

  it("returns to absent when the dashboard restarts without the store", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(pageResponse({ events: [], next_cursor: 1 }))
      .mockResolvedValueOnce(pageResponse({ events: [wire(1)], next_cursor: 1 }))
      .mockResolvedValueOnce(new Response("no", { status: 404 }));
    const modes: SpineProvenance[] = [];
    const source = createEventsTailSource({ fetcher, pollMs: 1000 });
    source.subscribeMode((mode) => modes.push(mode));
    await vi.waitFor(() => {
      expect(modes.at(-1)).toBe("hub");
    });
    await vi.advanceTimersByTimeAsync(1000);
    await vi.waitFor(() => {
      expect(modes.at(-1)).toBe("absent");
    });
    source.stop();
  });

  it("reports error on failures before and after catch-up, staying absent while absent", async () => {
    vi.useFakeTimers();
    const failing = vi.fn<typeof fetch>().mockResolvedValue(new Response("boom", { status: 500 }));
    const failedModes: SpineProvenance[] = [];
    const failingSource = createEventsTailSource({ fetcher: failing, pollMs: 1000 });
    failingSource.subscribeMode((mode) => failedModes.push(mode));
    await vi.waitFor(() => {
      expect(failedModes.at(-1)).toBe("error");
    });
    failingSource.stop();

    const junk = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(pageResponse({ events: [], next_cursor: 1 }))
      .mockResolvedValueOnce(pageResponse({ events: [wire(1)], next_cursor: 1 }))
      .mockResolvedValueOnce(new Response(JSON.stringify([1])));
    const junkModes: SpineProvenance[] = [];
    const junkSource = createEventsTailSource({ fetcher: junk, pollMs: 1000 });
    junkSource.subscribeMode((mode) => junkModes.push(mode));
    await vi.waitFor(() => {
      expect(junkModes.at(-1)).toBe("hub");
    });
    await vi.advanceTimersByTimeAsync(1000);
    await vi.waitFor(() => {
      expect(junkModes.at(-1)).toBe("error");
    });
    junkSource.stop();

    const absentThenFail = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("no", { status: 404 }))
      .mockRejectedValueOnce(new Error("torn down"));
    const absentModes: SpineProvenance[] = [];
    const absentSource = createEventsTailSource({ fetcher: absentThenFail, pollMs: 1000, absentPollMs: 1000 });
    absentSource.subscribeMode((mode) => absentModes.push(mode));
    await vi.waitFor(() => {
      expect(absentModes.at(-1)).toBe("absent");
    });
    await vi.advanceTimersByTimeAsync(1000);
    await vi.advanceTimersByTimeAsync(10);
    // The failure while absent does not invent an error state: still absent.
    expect(absentModes.at(-1)).toBe("absent");
    absentSource.stop();
  });

  it("stops cleanly: no emission, no scheduling, listeners released", async () => {
    vi.useFakeTimers();
    let resolveFetch: ((response: Response) => void) | undefined;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    const events: CockpitEvent[] = [];
    const source = createEventsTailSource({ fetcher, pollMs: 1000 });
    const unsubscribe = source.subscribe((event) => events.push(event));
    source.stop();
    resolveFetch?.(pageResponse({ events: [wire(1)], next_cursor: 1 }));
    await vi.advanceTimersByTimeAsync(10);
    expect(events).toEqual([]);
    expect(fetcher).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("drops a late failure that lands after stop", async () => {
    vi.useFakeTimers();
    let rejectFetch: ((reason: Error) => void) | undefined;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(
      () =>
        new Promise<Response>((_resolve, reject) => {
          rejectFetch = reject;
        }),
    );
    const modes: SpineProvenance[] = [];
    const source = createEventsTailSource({ fetcher, pollMs: 1000 });
    source.subscribeMode((mode) => modes.push(mode));
    source.stop();
    rejectFetch?.(new Error("torn down"));
    await vi.advanceTimersByTimeAsync(10);
    expect(modes.at(-1)).toBe("connecting");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("emits nothing when stopped between the tip and the backfill", async () => {
    vi.useFakeTimers();
    const source = createEventsTailSource({
      fetcher: vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(pageResponse({ events: [], next_cursor: 2 }))
        .mockImplementationOnce(() => {
          source.stop();
          return Promise.resolve(pageResponse({ events: [wire(1), wire(2)], next_cursor: 2 }));
        }),
      pollMs: 1000,
    });
    const events: CockpitEvent[] = [];
    source.subscribe((event) => events.push(event));
    await vi.advanceTimersByTimeAsync(50);
    expect(events).toEqual([]);
  });

  it("unsubscribing an event listener stops its delivery while others continue", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(pageResponse({ events: [], next_cursor: 1 }))
      .mockResolvedValueOnce(pageResponse({ events: [wire(1)], next_cursor: 1 }))
      .mockResolvedValueOnce(pageResponse({ events: [wire(2)], next_cursor: 2 }));
    const first: CockpitEvent[] = [];
    const second: CockpitEvent[] = [];
    const source = createEventsTailSource({ fetcher, pollMs: 1000 });
    const unsubscribeFirst = source.subscribe((event) => first.push(event));
    source.subscribe((event) => second.push(event));
    await vi.waitFor(() => {
      expect(second).toHaveLength(1);
    });
    unsubscribeFirst();
    await vi.advanceTimersByTimeAsync(1000);
    await vi.waitFor(() => {
      expect(second).toHaveLength(2);
    });
    expect(first).toHaveLength(1);
    source.stop();
  });

  it("runs on its defaults: the relative URL through the global fetch reports error", async () => {
    vi.useFakeTimers();
    const modes: SpineProvenance[] = [];
    const source = createEventsTailSource();
    const unsubscribeMode = source.subscribeMode((mode) => modes.push(mode));
    await vi.waitFor(() => {
      expect(modes.at(-1)).toBe("error");
    });
    unsubscribeMode();
    source.stop();
  });
});
