// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — slow cockpit report lifecycle contracts

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AUXILIARY_FEED_START_FALLBACK_MS,
  HEAVY_FEED_STAGGER_FALLBACK_MS,
  useCockpitAuxiliaryFeeds,
} from "../../src/hooks/useCockpitAuxiliaryFeeds";

const PRIMARY_PATHS = [
  "/reliability.json",
  "/federation.json",
  "/metrics.json",
  "/sessions.json",
  "/waits.json",
] as const;

function urlOf(input: RequestInfo | URL): string {
  return String(input);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useCockpitAuxiliaryFeeds", () => {
  it("stays inert while access is blocked", async () => {
    const fetcher = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetcher);
    const { result } = renderHook(() => useCockpitAuxiliaryFeeds(true, 1));

    act(() => result.current.start());
    await act(async () => Promise.resolve());

    expect(fetcher).not.toHaveBeenCalled();
    expect(result.current.reliability.status).toBe("connecting");
    expect(result.current.anomalyReport.status).toBe("connecting");
  });

  it("starts primary reports once and waits for reliability before anomalies", async () => {
    let resolveReliability!: (response: Response) => void;
    const reliability = new Promise<Response>((resolve) => {
      resolveReliability = resolve;
    });
    const fetcher = vi.fn<typeof fetch>((input) =>
      urlOf(input).startsWith("/reliability.json")
        ? reliability
        : Promise.resolve(new Response("absent", { status: 404 })),
    );
    vi.stubGlobal("fetch", fetcher);
    const { result } = renderHook(() => useCockpitAuxiliaryFeeds(false, 1));

    act(() => {
      result.current.start();
      result.current.start();
    });
    await waitFor(() =>
      expect(
        PRIMARY_PATHS.every((path) =>
          fetcher.mock.calls.some(([input]) => urlOf(input).startsWith(path)),
        ),
      ).toBe(true),
    );
    expect(
      fetcher.mock.calls.some(([input]) => urlOf(input).startsWith("/health-anomalies.json")),
    ).toBe(false);
    for (const path of PRIMARY_PATHS) {
      expect(fetcher.mock.calls.filter(([input]) => urlOf(input).startsWith(path))).toHaveLength(1);
    }

    resolveReliability(new Response("absent", { status: 404 }));
    await waitFor(() =>
      expect(
        fetcher.mock.calls.some(([input]) => urlOf(input).startsWith("/health-anomalies.json")),
      ).toBe(true),
    );
    expect(result.current.reliability.status).toBe("absent");
  });

  it("uses bounded fallbacks for both startup gates", async () => {
    vi.useFakeTimers();
    let resolveReliability!: (response: Response) => void;
    const reliability = new Promise<Response>((resolve) => {
      resolveReliability = resolve;
    });
    const fetcher = vi.fn<typeof fetch>((input) =>
      urlOf(input).startsWith("/reliability.json")
        ? reliability
        : Promise.resolve(new Response("absent", { status: 404 })),
    );
    vi.stubGlobal("fetch", fetcher);
    renderHook(() => useCockpitAuxiliaryFeeds(false, 1));

    expect(fetcher).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUXILIARY_FEED_START_FALLBACK_MS);
    });
    expect(fetcher.mock.calls.some(([input]) => urlOf(input).startsWith("/reliability.json"))).toBe(true);
    expect(
      fetcher.mock.calls.some(([input]) => urlOf(input).startsWith("/health-anomalies.json")),
    ).toBe(false);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(HEAVY_FEED_STAGGER_FALLBACK_MS);
    });
    expect(
      fetcher.mock.calls.some(([input]) => urlOf(input).startsWith("/health-anomalies.json")),
    ).toBe(true);

    resolveReliability(new Response("absent", { status: 404 }));
    await act(async () => Promise.resolve());
    expect(
      fetcher.mock.calls.filter(([input]) => urlOf(input).startsWith("/health-anomalies.json")),
    ).toHaveLength(1);
  });

  it("cancels the dormant startup timer on unmount", () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetcher);
    const { unmount } = renderHook(() => useCockpitAuxiliaryFeeds(false, 1));

    unmount();
    vi.advanceTimersByTime(AUXILIARY_FEED_START_FALLBACK_MS);

    expect(fetcher).not.toHaveBeenCalled();
  });

  it("resets state and cancels an old generation when credentials change", async () => {
    let resolveReliability!: (response: Response) => void;
    const oldReliability = new Promise<Response>((resolve) => {
      resolveReliability = resolve;
    });
    const fetcher = vi.fn<typeof fetch>((input) => {
      if (urlOf(input).startsWith("/reliability.json") && fetcher.mock.calls.length <= 5) {
        return oldReliability;
      }
      return Promise.resolve(new Response("absent", { status: 404 }));
    });
    vi.stubGlobal("fetch", fetcher);
    const { result, rerender } = renderHook(
      ({ revision }) => useCockpitAuxiliaryFeeds(false, revision),
      { initialProps: { revision: 1 } },
    );

    act(() => result.current.start());
    await waitFor(() => expect(fetcher).toHaveBeenCalled());
    rerender({ revision: 2 });
    expect(result.current.reliability.status).toBe("connecting");

    resolveReliability(new Response(JSON.stringify({ note: "stale generation" })));
    await act(async () => Promise.resolve());
    expect(result.current.reliability.status).toBe("connecting");

    act(() => result.current.start());
    await waitFor(() => expect(result.current.reliability.status).toBe("absent"));
  });
});
