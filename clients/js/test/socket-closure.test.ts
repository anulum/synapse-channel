// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — prior close handshake and failure boundary tests

import { afterEach, describe, expect, it, vi } from "vitest";
import type { WebSocketLike } from "../src/client.js";
import { SocketClosureBarrier } from "../src/socket-closure.js";

/** Control close completion separately from its request for lifecycle fault tests. */
class ClosingSocket implements WebSocketLike {
  onopen: ((event: unknown) => void) | null = null;
  onclose: ((event: unknown) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  sent: string[] = [];
  requested = false;

  /** Retain writes so the test socket has observable transport behavior. */
  send(data: string): void { this.sent.push(data); }
  /** Record a close request without prematurely emitting completion. */
  close(): void { this.requested = true; }
  /** Publish the peer close event when its handshake actually finishes. */
  finish(): void { this.onclose?.({}); }
}

afterEach(() => vi.useRealTimers());

describe("SocketClosureBarrier", () => {
  it.each([0, -1, NaN, Infinity])("rejects an invalid deadline %s", (deadline) => {
    expect(() => new SocketClosureBarrier(deadline)).toThrow(/positive and finite/);
  });
  it("settles only on close completion and preserves the prior callback", async () => {
    const barrier = new SocketClosureBarrier();
    const socket = new ClosingSocket();
    const callback = vi.fn();
    socket.onclose = callback;
    barrier.close(socket);
    const pending = barrier.pending;
    expect(socket.requested).toBe(true);
    expect(pending).not.toBeNull();
    expect(() => barrier.close(new ClosingSocket())).toThrow(/already pending/);
    socket.finish();
    await pending;
    expect(callback).toHaveBeenCalledOnce();
    expect(barrier.pending).toBeNull();
  });
  it("observes a synchronous close event before close returns", () => {
    const barrier = new SocketClosureBarrier();
    const socket = new ClosingSocket();
    socket.close = () => socket.finish();
    barrier.close(socket);
    expect(barrier.pending).toBeNull();
  });
  it("keeps a timed-out close blocked until the actual event arrives", async () => {
    vi.useFakeTimers();
    const barrier = new SocketClosureBarrier(10);
    const socket = new ClosingSocket();
    barrier.close(socket);
    const pending = barrier.pending;
    vi.advanceTimersByTime(10);
    await expect(pending).rejects.toThrow(/did not close in time/);
    expect(barrier.pending).toBe(pending);
    socket.finish();
    expect(barrier.pending).toBeNull();
  });
  it("retains a close failure until the socket eventually finishes", async () => {
    const barrier = new SocketClosureBarrier();
    const socket = new ClosingSocket();
    socket.close = () => { throw new Error("close failed"); };
    expect(() => barrier.close(socket)).toThrow(/close failed/);
    await expect(barrier.pending).rejects.toThrow(/close failed/);
    socket.finish();
    expect(barrier.pending).toBeNull();
  });
  it("settles even when an old callback fails", async () => {
    const barrier = new SocketClosureBarrier();
    const socket = new ClosingSocket();
    socket.onclose = () => { throw new Error("callback failed"); };
    barrier.close(socket);
    const pending = barrier.pending;
    expect(() => socket.finish()).toThrow(/callback failed/);
    await pending;
    expect(barrier.pending).toBeNull();
  });
  it("ignores duplicate old close events while observing a newer socket", async () => {
    const barrier = new SocketClosureBarrier();
    const first = new ClosingSocket();
    barrier.close(first);
    first.finish();
    const second = new ClosingSocket();
    barrier.close(second);
    const pending = barrier.pending;
    first.finish();
    expect(barrier.pending).toBe(pending);
    second.finish();
    await pending;
    expect(barrier.pending).toBeNull();
  });
});
