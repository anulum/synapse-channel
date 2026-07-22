// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — multiplexed live-transport client tests

import { describe, expect, it, vi } from "vitest";

import {
  createLiveTransport,
  MAX_LIVE_FRAME_BYTES,
  parseLiveFrame,
  type LiveChannelFrame,
  type LiveConnectionState,
} from "../src/lib/liveTransport";

function line(
  sequence: number,
  kind: "hello" | "channel" | "close",
  extra: Record<string, unknown> = {},
): string {
  return `${JSON.stringify({
    version: 1,
    connection_id: "stream-a",
    sequence,
    kind,
    sent_at: 10,
    ...extra,
  })}\n`;
}

function streamResponse(chunks: readonly string[], status = 200): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }),
    { status, headers: { "Content-Type": "application/x-ndjson" } },
  );
}

async function settle(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

describe("parseLiveFrame", () => {
  it("accepts exact hello and channel envelopes", () => {
    expect(parseLiveFrame(line(1, "hello").trim())).toMatchObject({
      connectionId: "stream-a",
      sequence: 1,
      kind: "hello",
    });
    expect(
      parseLiveFrame(
        line(2, "channel", { channel: "events", status: "live", data: { events: [] } }).trim(),
      ),
    ).toMatchObject({
      connectionId: "stream-a",
      sequence: 2,
      kind: "channel",
      channel: "events",
      status: "live",
    });
  });

  it.each([
    "not-json",
    "[]",
    JSON.stringify({ version: 2 }),
    line(0, "hello").trim(),
    line(1, "channel", { channel: "unknown", status: "live" }).trim(),
    line(1, "channel", { channel: "events", status: "maybe" }).trim(),
  ])("rejects malformed or unsupported frames: %s", (raw) => {
    expect(parseLiveFrame(raw)).toBeNull();
  });

  it.each([
    { connection_id: "", sequence: 1, kind: "hello", sent_at: 1 },
    { connection_id: "x", sequence: "1", kind: "hello", sent_at: 1 },
    { connection_id: "x", sequence: 1.5, kind: "hello", sent_at: 1 },
    { connection_id: "x", sequence: 1, kind: "unknown", sent_at: 1 },
    { connection_id: "x", sequence: 1, kind: "hello", sent_at: "1" },
    { connection_id: "x", sequence: 1, kind: "hello", sent_at: Number.NaN },
    { connection_id: "x", sequence: 1, kind: "hello", sent_at: -1 },
  ])("rejects an invalid required envelope field: %o", (fields) => {
    expect(parseLiveFrame(JSON.stringify({ version: 1, ...fields }))).toBeNull();
  });

  it.each([
    ["snapshot", "live"],
    ["snapshot", "unchanged"],
    ["events", "absent"],
    ["receipts", "error"],
    ["operator_actions", "live"],
  ] as const)("accepts the %s channel with %s status", (channel, status) => {
    expect(
      parseLiveFrame(
        line(1, "channel", { channel, status, detail: "bounded", data: null }).trim(),
      ),
    ).toMatchObject({ channel, status, detail: "bounded", data: null });
  });

  it("rejects unchanged status on a non-snapshot channel", () => {
    expect(
      parseLiveFrame(
        line(1, "channel", { channel: "events", status: "unchanged" }).trim(),
      ),
    ).toBeNull();
  });

  it("does not invent channel data when the envelope omits it", () => {
    expect(
      parseLiveFrame(line(1, "channel", { channel: "snapshot", status: "unchanged" }).trim()),
    ).not.toHaveProperty("data");
  });

  it("preserves optional close metadata without inventing absent fields", () => {
    expect(
      parseLiveFrame(
        line(1, "close", { status: "complete", detail: "done", data: { cycles: 1 } }).trim(),
      ),
    ).toMatchObject({ kind: "close", status: "complete", detail: "done", data: { cycles: 1 } });
  });
});

describe("createLiveTransport", () => {
  it("uses the authenticated default fetcher and live endpoint", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 404 }));
    vi.stubGlobal("fetch", fetcher);
    const transport = createLiveTransport();

    await settle();
    transport.stop();
    vi.unstubAllGlobals();

    expect(fetcher).toHaveBeenCalledWith(
      "/live.ndjson",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
  });

  it("delivers split multiplexed frames and reconnects after a clean close", async () => {
    const channel = line(2, "channel", {
      channel: "snapshot",
      status: "live",
      data: { hub_id: "h" },
    });
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        streamResponse([line(1, "hello") + channel.slice(0, 15), channel.slice(15)]),
      )
      .mockResolvedValue(new Response(null, { status: 404 }));
    const waits: number[] = [];
    const transport = createLiveTransport({
      fetcher,
      minimumBackoffMs: 10,
      wait: async (milliseconds) => {
        waits.push(milliseconds);
      },
    });
    const frames: LiveChannelFrame[] = [];
    const states: LiveConnectionState[] = [];
    transport.subscribeFrames((frame) => frames.push(frame));
    transport.subscribeState((state) => states.push(state));

    await settle();
    await settle();
    transport.stop();

    expect(frames).toHaveLength(1);
    expect(frames[0]).toMatchObject({ channel: "snapshot", status: "live" });
    expect(states.map((state) => state.status)).toContain("reconnecting");
    expect(states.map((state) => state.status)).toContain("unsupported");
    expect(waits).toEqual([10]);
  });

  it("reports a sequence gap and does not publish the out-of-order frame", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        streamResponse([
          line(1, "hello"),
          line(3, "channel", { channel: "events", status: "live", data: {} }),
        ]),
      )
      .mockResolvedValue(new Response(null, { status: 404 }));
    const transport = createLiveTransport({ fetcher, wait: async () => undefined });
    const frames: LiveChannelFrame[] = [];
    const states: LiveConnectionState[] = [];
    transport.subscribeFrames((frame) => frames.push(frame));
    transport.subscribeState((state) => states.push(state));

    await settle();
    await settle();
    transport.stop();

    expect(frames).toEqual([]);
    expect(states.some((state) => state.status === "gap")).toBe(true);
  });

  it("reports a connection-id change as a gap", async () => {
    const changed = JSON.stringify({
      version: 1,
      connection_id: "stream-b",
      sequence: 2,
      kind: "channel",
      sent_at: 11,
      channel: "events",
      status: "live",
      data: {},
    });
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(streamResponse([line(1, "hello"), `${changed}\n`]))
      .mockResolvedValue(new Response(null, { status: 404 }));
    const transport = createLiveTransport({ fetcher, wait: async () => undefined });
    const states: LiveConnectionState[] = [];
    transport.subscribeState((state) => states.push(state));

    await settle();
    await settle();
    transport.stop();

    expect(states.some((state) => state.status === "gap")).toBe(true);
  });

  it.each([
    [new Response("failed", { status: 503 }), "returned 503"],
    [new Response(null, { status: 200 }), "had no body"],
    [streamResponse(["invalid\n"]), "invalid frame"],
    [streamResponse(["\n"]), "closed"],
  ] as const)("reconnects after %s", async (response, detail) => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(response)
      .mockResolvedValue(new Response(null, { status: 404 }));
    const transport = createLiveTransport({ fetcher, wait: async () => undefined });
    const states: LiveConnectionState[] = [];
    transport.subscribeState((state) => states.push(state));

    await settle();
    await settle();
    transport.stop();

    expect(states.some((state) => state.detail?.includes(detail) === true)).toBe(true);
  });

  it("stringifies non-Error failures and caps exponential backoff", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce("offline")
      .mockRejectedValueOnce(new Error("still offline"))
      .mockResolvedValue(new Response(null, { status: 404 }));
    const waits: number[] = [];
    const transport = createLiveTransport({
      fetcher,
      minimumBackoffMs: 20,
      maximumBackoffMs: 25,
      wait: async (milliseconds) => {
        waits.push(milliseconds);
      },
    });
    const states: LiveConnectionState[] = [];
    transport.subscribeState((state) => states.push(state));

    await settle();
    await settle();
    transport.stop();

    expect(states.some((state) => state.detail === "offline")).toBe(true);
    expect(waits).toEqual([20, 25]);
  });

  it("fails closed on oversized unterminated frames", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(streamResponse(["x".repeat(MAX_LIVE_FRAME_BYTES + 1)]))
      .mockResolvedValue(new Response(null, { status: 404 }));
    const transport = createLiveTransport({ fetcher, wait: async () => undefined });
    const states: LiveConnectionState[] = [];
    transport.subscribeState((state) => states.push(state));

    await settle();
    await settle();
    transport.stop();

    expect(states.some((state) => state.detail?.includes("byte limit") === true)).toBe(true);
  });

  it("stops an active request idempotently", async () => {
    let signal: AbortSignal | undefined;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_input, init) => {
      signal = init?.signal ?? undefined;
      return await new Promise<Response>(() => undefined);
    });
    const transport = createLiveTransport({ fetcher });
    const states: LiveConnectionState[] = [];
    transport.subscribeState((state) => states.push(state));

    transport.stop();
    transport.stop();

    expect(signal?.aborted).toBe(true);
    expect(states.at(-1)?.status).toBe("stopped");
  });

  it("returns cleanly when a pending stream closes after stop", async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const response = new Response(
      new ReadableStream({
        start(candidate) {
          controller = candidate;
        },
      }),
    );
    const transport = createLiveTransport({ fetcher: vi.fn<typeof fetch>().mockResolvedValue(response) });

    await settle();
    transport.stop();
    controller.close();
    await settle();
  });

  it("returns cleanly when abort rejects an active fetch", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(
      async (_input, init) =>
        await new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
        }),
    );
    const transport = createLiveTransport({ fetcher });

    await settle();
    transport.stop();
    await settle();
  });

  it("aborts the default reconnect wait and honours listener unsubscription", async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error("offline"));
    const transport = createLiveTransport({ fetcher, minimumBackoffMs: 10_000 });
    const frameListener = vi.fn();
    const stateListener = vi.fn();
    const unsubscribeFrames = transport.subscribeFrames(frameListener);
    const unsubscribeState = transport.subscribeState(stateListener);
    unsubscribeFrames();
    unsubscribeState();
    await settle();

    transport.stop();
    await settle();

    expect(frameListener).not.toHaveBeenCalled();
    expect(stateListener).toHaveBeenCalledOnce();
  });
});
