// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — replay reconstruction lifecycle tests

import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useCockpitReplay } from "../../src/hooks/useCockpitReplay";
import type { ReplayState } from "../../src/lib/workspace";

interface ReplayProps {
  readonly blocked: boolean;
  readonly replay: ReplayState;
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useCockpitReplay", () => {
  it("loads compare slots through the production state-at client and clears on lock", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>((input) => {
      const sequence = new URL(String(input), "http://localhost").searchParams.get("seq");
      return Promise.resolve(new Response(JSON.stringify({
        as_of_seq: Number(sequence),
        log_end_seq: 9,
        note: "claims and tasks reconstructed",
        state: { generated_at: 100, active_claims: [] },
        board: { tasks: [] },
      })));
    });
    vi.stubGlobal("fetch", fetcher);
    const setReplay = vi.fn<(replay: ReplayState) => void>();
    const { result, rerender } = renderHook(
      ({ blocked, replay }: ReplayProps) => useCockpitReplay({
        blocked,
        replay,
        maximumSequence: 9,
        setReplay,
      }),
      { initialProps: { blocked: false, replay: { mode: "compare", a: 3, b: 7 } } },
    );

    expect(result.current.slotA).toEqual({ seq: 3, state: null, note: null });
    expect(result.current.slotB).toEqual({ seq: 7, state: null, note: null });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(result.current.slotA?.state?.asOfSeq).toBe(3);
    expect(result.current.slotB?.state?.asOfSeq).toBe(7);
    expect(result.current.travelling).toBe(true);

    act(() => result.current.toggleTravel());
    expect(setReplay).toHaveBeenCalledWith({ mode: "live" });
    rerender({ blocked: true, replay: { mode: "compare", a: 3, b: 7 } });
    expect(result.current.slotA).toBeNull();
    expect(result.current.slotB).toBeNull();
    expect(result.current.travelling).toBe(false);
  });

  it("reports an absent reconstruction endpoint and starts travel at retained max", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn<typeof fetch>(() =>
      Promise.resolve(new Response("missing", { status: 404 })),
    ));
    const setReplay = vi.fn<(replay: ReplayState) => void>();
    const historyReplay: ReplayState = { mode: "history", at: 12 };
    const { result } = renderHook(() => useCockpitReplay({
      blocked: false,
      replay: historyReplay,
      maximumSequence: null,
      setReplay,
    }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    expect(result.current.slotA).toBeNull();
    expect(result.current.slotB).toEqual({
      seq: 12,
      state: null,
      note: "state-at surface not served (--feeds-db)",
    });

    const liveReplay: ReplayState = { mode: "live" };
    const { result: live } = renderHook(() => useCockpitReplay({
      blocked: false,
      replay: liveReplay,
      maximumSequence: null,
      setReplay,
    }));
    act(() => live.current.toggleTravel());
    expect(setReplay).toHaveBeenLastCalledWith({ mode: "history", at: 0 });
  });

  it("ignores a superseded response and reports the current request error", async () => {
    vi.useFakeTimers();
    let resolveFirst: ((response: Response) => void) | undefined;
    let request = 0;
    vi.stubGlobal("fetch", vi.fn<typeof fetch>(() => {
      request += 1;
      if (request === 1) {
        return new Promise<Response>((resolve) => {
          resolveFirst = resolve;
        });
      }
      return Promise.resolve(new Response("error", { status: 500 }));
    }));
    const setReplay = vi.fn<(replay: ReplayState) => void>();
    const { result, rerender } = renderHook(
      ({ replay }: { readonly replay: ReplayState }) => useCockpitReplay({
        blocked: false,
        replay,
        maximumSequence: 20,
        setReplay,
      }),
      { initialProps: { replay: { mode: "history", at: 1 } } },
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    rerender({ replay: { mode: "history", at: 2 } });
    await act(async () => {
      resolveFirst?.(new Response(JSON.stringify({
        as_of_seq: 1,
        log_end_seq: 20,
        state: { generated_at: 1, active_claims: [] },
        board: { tasks: [] },
      })));
      await Promise.resolve();
    });
    expect(result.current.slotB).toEqual({ seq: 2, state: null, note: null });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    expect(result.current.slotB).toEqual({
      seq: 2,
      state: null,
      note: "hub returned 500",
    });
  });
});
