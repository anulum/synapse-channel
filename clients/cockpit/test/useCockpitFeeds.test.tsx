// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — authenticated cockpit feed startup ordering tests

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AUXILIARY_FEED_START_FALLBACK_MS,
  useCockpitFeeds,
} from "../src/hooks/useCockpitFeeds";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const AUXILIARY_PATHS = [
  "/reliability.json",
  "/federation.json",
  "/metrics.json",
  "/sessions.json",
  "/waits.json",
  "/health-anomalies.json",
  "/receipts.json",
  "/operator-actions.json",
] as const;

function urlOf(input: RequestInfo | URL): string {
  return String(input);
}

function hasAuxiliaryRequest(fetcher: ReturnType<typeof vi.fn>): boolean {
  return fetcher.mock.calls.some(([input]) => AUXILIARY_PATHS.some((path) => urlOf(input).startsWith(path)));
}

function FeedProbe(): React.JSX.Element {
  const feeds = useCockpitFeeds(false, 0);
  return (
    <output aria-label="feed probe">
      {feeds.provenance}:{feeds.log.length}
    </output>
  );
}

describe("useCockpitFeeds startup", () => {
  it("lets the exact event history settle before starting auxiliary reports", async () => {
    let resolveEvents!: (response: Response) => void;
    const eventResponse = new Promise<Response>((resolve) => {
      resolveEvents = resolve;
    });
    const fetcher = vi.fn<typeof fetch>((input) =>
      urlOf(input).startsWith("/events.json")
        ? eventResponse
        : Promise.resolve(new Response("absent", { status: 404 })),
    );
    vi.stubGlobal("fetch", fetcher);

    render(<FeedProbe />);
    await waitFor(() =>
      expect(fetcher.mock.calls.some(([input]) => urlOf(input).startsWith("/events.json"))).toBe(true),
    );
    expect(hasAuxiliaryRequest(fetcher)).toBe(false);

    resolveEvents(
      new Response(
        JSON.stringify({
          events: [
            {
              seq: 7,
              ts: 7,
              kind: "chat",
              payload: { sender: "alpha/one", target: "beta/two", payload: "hello" },
            },
          ],
          next_cursor: 7,
          history_included: true,
        }),
      ),
    );
    await waitFor(() => expect(screen.getByLabelText("feed probe").textContent).toBe("hub:1"));
    await waitFor(() => expect(hasAuxiliaryRequest(fetcher)).toBe(true));
  });

  it("starts auxiliary reports after a bounded grace period when the event endpoint hangs", async () => {
    vi.useFakeTimers();
    const never = new Promise<Response>(() => undefined);
    const fetcher = vi.fn<typeof fetch>((input) =>
      urlOf(input).startsWith("/events.json")
        ? never
        : Promise.resolve(new Response("absent", { status: 404 })),
    );
    vi.stubGlobal("fetch", fetcher);

    render(<FeedProbe />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(hasAuxiliaryRequest(fetcher)).toBe(false);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUXILIARY_FEED_START_FALLBACK_MS);
    });
    expect(hasAuxiliaryRequest(fetcher)).toBe(true);
  });
});
