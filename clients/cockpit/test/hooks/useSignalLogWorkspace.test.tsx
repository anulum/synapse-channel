// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — signal-log workspace lifecycle contracts

import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useSignalLogWorkspace } from "../../src/hooks/useSignalLogWorkspace";
import { OPEN_QUERY } from "../../src/lib/logQuery";
import type { CockpitEvent } from "../../src/types";

function eventOf(seq: number, overrides: Partial<CockpitEvent> = {}): CockpitEvent {
  return {
    seq,
    ts: 1_751_800_000 + seq,
    kind: "claim",
    lane: "claims",
    severity: 0.4,
    actor: `agent-${seq}`,
    label: `event ${seq}`,
    taskId: `task-${seq}`,
    ...overrides,
  };
}

function tail(toSeq: number): object {
  return {
    events: [
      { seq: toSeq - 1, ts: 1_751_800_000 + toSeq - 1, kind: "chat", payload: { text: "hello" } },
      { seq: toSeq, ts: 1_751_800_000 + toSeq, kind: "task", payload: { task_id: "t-1", status: "done" } },
    ],
    next_cursor: toSeq,
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useSignalLogWorkspace", () => {
  it("freezes the live evidence, counts arrivals, and applies the brush and query", () => {
    const first = [eventOf(2), eventOf(1, { actor: "outside", ts: 10 })];
    const { result, rerender } = renderHook(
      ({ events, text }) => useSignalLogWorkspace({
        events,
        window: { fromTs: 1_751_800_000, toTs: 1_751_800_100 },
        query: { ...OPEN_QUERY, text },
        provenance: "derived",
      }),
      { initialProps: { events: first, text: "" } },
    );

    expect(result.current.shown.map((event) => event.seq)).toEqual([2]);
    expect(result.current.actors).toEqual(["agent-2"]);
    act(() => result.current.togglePause());
    rerender({ events: [eventOf(3), ...first], text: "event 2" });
    expect(result.current.paused).toBe(true);
    expect(result.current.newerCount).toBe(1);
    expect(result.current.shown.map((event) => event.seq)).toEqual([2]);
    act(() => result.current.togglePause());
    expect(result.current.shown.map((event) => event.seq)).toEqual([2]);
  });

  it("enters, scrubs, pins, compares, and leaves attested history", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockImplementation((input) => {
      const url = String(input);
      const toSeq = url.includes("since=latest") ? 42 : url.includes("since=0") ? 42 : 25;
      return Promise.resolve(new Response(JSON.stringify(tail(toSeq)), { status: 200 }));
    });
    vi.stubGlobal("fetch", fetcher);
    const { result } = renderHook(() => useSignalLogWorkspace({
      events: [eventOf(1)],
      window: null,
      query: OPEN_QUERY,
      provenance: "hub",
    }));

    await act(async () => result.current.enterHistory());
    expect(result.current.historyOn).toBe(true);
    expect(result.current.historyLatest).toBe(42);
    expect(result.current.historyWindow?.toSeq).toBe(42);
    act(() => result.current.togglePinnedWindow());
    act(() => result.current.toggleDiff());
    expect(result.current.pinnedWindow?.toSeq).toBe(42);
    expect(result.current.diffOpen).toBe(true);

    act(() => result.current.scrubTo(25));
    expect(result.current.historyPos).toBe(25);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
      await Promise.resolve();
    });
    expect(result.current.historyWindow?.toSeq).toBe(25);

    act(() => result.current.togglePinnedWindow());
    expect(result.current.pinnedWindow).toBeNull();
    expect(result.current.diffOpen).toBe(false);
    act(() => result.current.leaveHistory());
    expect(result.current.historyOn).toBe(false);
    expect(result.current.shown[0]?.seq).toBe(1);
  });

  it("states unavailable history and validates post-mortem files", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("missing", { status: 404 })));
    const { result } = renderHook(() => useSignalLogWorkspace({
      events: [],
      window: null,
      query: OPEN_QUERY,
      provenance: "hub",
    }));

    await act(async () => result.current.enterHistory());
    expect(result.current.historyOn).toBe(false);
    expect(result.current.historyNote).toBe("event feed not served");

    await act(async () => result.current.openExportFile(new File(["bad"], "bad.json")));
    expect(result.current.postMortemNote).toBe("bad.json is not a cockpit export");

    const valid = new File([JSON.stringify({
      provenance: "derived",
      exported_at: "2026-07-23T00:00:00Z",
      events: [{
        seq: 9,
        ts: 1_751_800_009,
        kind: "claim",
        lane: "claims",
        actor: "file-agent",
        label: "file event",
      }],
    })], "valid.json");
    await act(async () => result.current.openExportFile(valid));
    expect(result.current.postMortem?.name).toBe("valid.json");
    expect(result.current.shown[0]?.label).toBe("file event");
    expect(result.current.shownProvenance).toBe("derived");
    act(() => result.current.closePostMortem());
    expect(result.current.postMortem).toBeNull();
    expect(result.current.postMortemNote).toBeNull();
  });

  it("cancels a pending scrub when history closes or the hook unmounts", () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetcher);
    const { result, unmount } = renderHook(() => useSignalLogWorkspace({
      events: [],
      window: null,
      query: OPEN_QUERY,
      provenance: "hub",
    }));

    act(() => result.current.scrubTo(10));
    act(() => result.current.leaveHistory());
    vi.advanceTimersByTime(250);
    expect(fetcher).not.toHaveBeenCalled();
    act(() => result.current.scrubTo(11));
    unmount();
    vi.advanceTimersByTime(250);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("handles empty and displaced frozen heads without losing the arrival count", () => {
    const { result, rerender } = renderHook(
      ({ events }) => useSignalLogWorkspace({
        events,
        window: null,
        query: OPEN_QUERY,
        provenance: "derived",
      }),
      { initialProps: { events: [] as CockpitEvent[] } },
    );
    act(() => result.current.togglePause());
    rerender({ events: [eventOf(1)] });
    expect(result.current.newerCount).toBe(1);
    act(() => result.current.togglePause());

    rerender({ events: [eventOf(2)] });
    act(() => result.current.togglePause());
    rerender({ events: [eventOf(3)] });
    expect(result.current.newerCount).toBe(1);
  });

  it("states latest and window fetch errors and ignores a pin without a window", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("error", { status: 500 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(tail(10)), { status: 200 }))
      .mockResolvedValueOnce(new Response("missing", { status: 404 }));
    vi.stubGlobal("fetch", fetcher);
    const { result } = renderHook(() => useSignalLogWorkspace({
      events: [],
      window: null,
      query: OPEN_QUERY,
      provenance: "hub",
    }));

    act(() => result.current.togglePinnedWindow());
    expect(result.current.pinnedWindow).toBeNull();
    await act(async () => result.current.enterHistory());
    expect(result.current.historyNote).toBe("hub returned 500");
    await act(async () => result.current.enterHistory());
    expect(result.current.historyOn).toBe(true);
    expect(result.current.historyNote).toBe("event feed not served");

    const valid = new File([JSON.stringify({ provenance: "hub", events: [] })], "empty.json");
    await act(async () => result.current.openExportFile(valid));
    expect(result.current.postMortem?.name).toBe("empty.json");
    expect(result.current.historyOn).toBe(false);
  });

  it("replaces a queued scrub and states a failed replacement fetch", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response("missing", { status: 404 }));
    vi.stubGlobal("fetch", fetcher);
    const { result } = renderHook(() => useSignalLogWorkspace({
      events: [],
      window: null,
      query: OPEN_QUERY,
      provenance: "hub",
    }));

    act(() => {
      result.current.scrubTo(10);
      result.current.scrubTo(11);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
      await Promise.resolve();
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(result.current.historyNote).toBe("event feed not served");
  });

  it("shows an empty history workspace while its first window is still loading", async () => {
    let resolveWindow!: (response: Response) => void;
    const pendingWindow = new Promise<Response>((resolve) => {
      resolveWindow = resolve;
    });
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify(tail(12)), { status: 200 }))
      .mockReturnValueOnce(pendingWindow);
    vi.stubGlobal("fetch", fetcher);
    const { result } = renderHook(() => useSignalLogWorkspace({
      events: [eventOf(1)],
      window: null,
      query: OPEN_QUERY,
      provenance: "hub",
    }));

    let entering!: Promise<void>;
    act(() => {
      entering = result.current.enterHistory();
    });
    await act(async () => Promise.resolve());
    expect(result.current.historyOn).toBe(true);
    expect(result.current.historyWindow).toBeNull();
    expect(result.current.shown).toEqual([]);
    resolveWindow(new Response(JSON.stringify(tail(12)), { status: 200 }));
    await act(async () => entering);
    expect(result.current.historyWindow?.toSeq).toBe(12);
  });
});
