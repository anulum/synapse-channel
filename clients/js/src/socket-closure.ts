// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — serialize reconnect after socket closure

import type { WebSocketLike } from "./client.js";

/** Track one finite close handshake without discarding its identity boundary. */
export class SocketClosureBarrier {
  private closure: Promise<void> | null = null;

  /** Require a positive finite close observation deadline in milliseconds. */
  constructor(private readonly timeoutMs = 5000) {
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
      throw new Error("close deadline must be positive and finite");
    }
  }

  /** The prior close outcome, retained until its actual close event arrives. */
  get pending(): Promise<void> | null {
    return this.closure;
  }

  /** Observe close before invoking it; support synchronous transport callbacks. */
  close(socket: WebSocketLike): void {
    if (this.closure !== null) {
      throw new Error("a socket close is already pending");
    }
    let complete!: () => void;
    let fail!: (error: unknown) => void;
    const promise = new Promise<void>((resolve, reject) => {
      complete = resolve;
      fail = reject;
    });
    this.closure = promise;
    // close() remains void; a later connect still observes the rejected promise.
    void promise.catch(() => undefined);
    const timer = setTimeout(() => fail(new Error("prior socket did not close in time")), this.timeoutMs);
    const previous = socket.onclose;
    socket.onclose = (event) => {
      clearTimeout(timer);
      if (this.closure === promise) this.closure = null;
      try {
        previous?.(event);
      } finally {
        complete();
      }
    };
    try {
      socket.close();
    } catch (error) {
      clearTimeout(timer);
      fail(error);
      throw error;
    }
  }
}
