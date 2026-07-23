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
  LIVE_TRANSPORT_FALLBACK_MS,
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
    <>
      <output aria-label="feed probe">
        {feeds.provenance}:{feeds.log.length}
      </output>
      <output aria-label="transport probe">{feeds.transport.status}</output>
      <output aria-label="receipt probe">
        {feeds.receipts.data?.map((receipt) => receipt.subject).join(",") ?? ""}
      </output>
    </>
  );
}

function liveLine(
  sequence: number,
  kind: "hello" | "channel",
  extra: Record<string, unknown> = {},
): string {
  return `${JSON.stringify({
    version: 1,
    connection_id: "hook-stream",
    sequence,
    kind,
    sent_at: 1000,
    ...extra,
  })}\n`;
}

function liveResponse(lines: string, close = true): Response {
  const encoded = new TextEncoder().encode(lines);
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(encoded);
        if (close) controller.close();
      },
    }),
    { status: 200 },
  );
}

describe("useCockpitFeeds startup", () => {
  it("uses multiplexed history without starting legacy high-frequency polling", async () => {
    const never = new Promise<Response>(() => undefined);
    const fetcher = vi.fn<typeof fetch>((input) => {
      const url = urlOf(input);
      if (url === "/live.ndjson" && fetcher.mock.calls.filter(([entry]) => urlOf(entry) === url).length === 1) {
        return Promise.resolve(
          liveResponse(
            liveLine(1, "hello") +
              liveLine(2, "channel", {
                channel: "snapshot",
                status: "live",
                data: {},
              }) +
              liveLine(3, "channel", {
                channel: "snapshot",
                status: "unchanged",
              }) +
              liveLine(4, "channel", {
                channel: "events",
                status: "live",
                data: {
                  events: [
                    {
                      seq: 9,
                      ts: 9,
                      kind: "chat",
                      payload: { sender: "alpha/one", target: "beta/two", payload: "streamed" },
                    },
                  ],
                  next_cursor: 9,
                  history_included: true,
                },
              }) +
              liveLine(5, "channel", {
                channel: "receipts",
                status: "live",
                data: { present: true, receipts: [], next_cursor: 9 },
              }) +
              liveLine(6, "channel", {
                channel: "operator_actions",
                status: "live",
                data: { present: true, actions: [], next_cursor: 9 },
              }),
            false,
          ),
        );
      }
      if (url === "/live.ndjson") return never;
      return Promise.resolve(new Response("absent", { status: 404 }));
    });
    vi.stubGlobal("fetch", fetcher);

    render(<FeedProbe />);
    await waitFor(() => expect(screen.getByLabelText("feed probe").textContent).toBe("hub:1"));
    expect(screen.getByLabelText("transport probe").textContent).toBe("live");

    expect(fetcher.mock.calls.some(([input]) => urlOf(input).startsWith("/snapshot.json"))).toBe(false);
    expect(fetcher.mock.calls.some(([input]) => urlOf(input).startsWith("/events.json"))).toBe(false);
    expect(fetcher.mock.calls.some(([input]) => urlOf(input).startsWith("/receipts.json"))).toBe(false);
    expect(fetcher.mock.calls.some(([input]) => urlOf(input).startsWith("/operator-actions.json"))).toBe(false);
  });

  it("merges a later live receipt delta without starting the polling fallback", async () => {
    let stream!: ReadableStreamDefaultController<Uint8Array>;
    const fetcher = vi.fn<typeof fetch>((input) => {
      if (urlOf(input) !== "/live.ndjson") {
        return Promise.resolve(new Response("absent", { status: 404 }));
      }
      return Promise.resolve(
        new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              stream = controller;
              controller.enqueue(new TextEncoder().encode(liveLine(1, "hello")));
              controller.enqueue(
                new TextEncoder().encode(
                  liveLine(2, "channel", {
                    channel: "receipts",
                    status: "live",
                    data: {
                      present: true,
                      receipts: [
                        {
                          seq: 1,
                          ts: 1,
                          receipt_id: "delivery:1",
                          kind: "delivery",
                          subject: "seed",
                          actor: "operator/test",
                          status: "delivered",
                          summary: "seed delivered",
                          source_event_kind: "delivery_receipt_immediate",
                        },
                      ],
                      next_cursor: 1,
                    },
                  }),
                ),
              );
            },
          }),
          { status: 200 },
        ),
      );
    });
    vi.stubGlobal("fetch", fetcher);

    render(<FeedProbe />);
    await waitFor(() => expect(screen.getByLabelText("receipt probe").textContent).toBe("seed"));

    act(() => {
      stream.enqueue(
        new TextEncoder().encode(
          liveLine(3, "channel", {
            channel: "receipts",
            status: "live",
            data: {
              present: true,
              receipts: [
                {
                  seq: 3,
                  ts: 3,
                  receipt_id: "delivery:3",
                  kind: "delivery",
                  subject: "later",
                  actor: "operator/test",
                  status: "undelivered",
                  summary: "later undelivered",
                  source_event_kind: "delivery_receipt_immediate",
                },
              ],
              next_cursor: 3,
            },
          }),
        ),
      );
    });

    await waitFor(() => expect(screen.getByLabelText("receipt probe").textContent).toBe("later,seed"));
    expect(fetcher.mock.calls.some(([input]) => urlOf(input).startsWith("/receipts.json"))).toBe(false);
  });

  it("starts polling after the live stream remains disconnected past the grace window", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>((input) => {
      const url = urlOf(input);
      if (url === "/live.ndjson" && fetcher.mock.calls.filter(([entry]) => urlOf(entry) === url).length === 1) {
        return Promise.resolve(liveResponse(liveLine(1, "hello")));
      }
      if (url === "/live.ndjson") return Promise.reject(new Error("offline"));
      return Promise.resolve(new Response("absent", { status: 404 }));
    });
    vi.stubGlobal("fetch", fetcher);

    render(<FeedProbe />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(fetcher.mock.calls.some(([input]) => urlOf(input).startsWith("/snapshot.json"))).toBe(false);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIVE_TRANSPORT_FALLBACK_MS);
    });

    expect(fetcher.mock.calls.some(([input]) => urlOf(input).startsWith("/snapshot.json"))).toBe(true);
    expect(fetcher.mock.calls.some(([input]) => urlOf(input).startsWith("/events.json"))).toBe(true);
    expect(screen.getByLabelText("transport probe").textContent).toBe("fallback");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(screen.getByLabelText("transport probe").textContent).toBe("fallback");
  });

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

  it("does not run both whole-log reports concurrently", async () => {
    let resolveReliability!: (response: Response) => void;
    const reliabilityResponse = new Promise<Response>((resolve) => {
      resolveReliability = resolve;
    });
    const fetcher = vi.fn<typeof fetch>((input) => {
      const url = urlOf(input);
      if (url.startsWith("/reliability.json")) return reliabilityResponse;
      if (url.startsWith("/events.json")) {
        return Promise.resolve(new Response(JSON.stringify({ events: [], next_cursor: 0, history_included: true })));
      }
      return Promise.resolve(new Response("absent", { status: 404 }));
    });
    vi.stubGlobal("fetch", fetcher);

    render(<FeedProbe />);
    await waitFor(() =>
      expect(fetcher.mock.calls.some(([input]) => urlOf(input).startsWith("/reliability.json"))).toBe(true),
    );
    expect(fetcher.mock.calls.some(([input]) => urlOf(input).startsWith("/health-anomalies.json"))).toBe(false);

    resolveReliability(new Response("absent", { status: 404 }));
    await waitFor(() =>
      expect(fetcher.mock.calls.some(([input]) => urlOf(input).startsWith("/health-anomalies.json"))).toBe(true),
    );
  });
});
