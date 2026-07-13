// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — timer ownership for the editor hub transport

/** Keep transport timers isolated from WebSocket and protocol state changes. */

/** Mutually managed timers for one transport lifecycle. */
export class HubTransportTimers {
  private retryTimer: ReturnType<typeof setTimeout> | undefined;
  private probeTimer: ReturnType<typeof setInterval> | undefined;
  private freshnessTimer: ReturnType<typeof setInterval> | undefined;
  private welcomeTimer: ReturnType<typeof setTimeout> | undefined;

  /** Arm the deadline for the initial authenticated welcome frame. */
  startWelcome(delayMs: number, onTimeout: () => void): void {
    this.clearWelcome();
    this.welcomeTimer = setTimeout(onTimeout, delayMs);
  }

  /** Cancel the welcome deadline after negotiation has completed. */
  clearWelcome(): void {
    if (this.welcomeTimer !== undefined) {
      clearTimeout(this.welcomeTimer);
      this.welcomeTimer = undefined;
    }
  }

  /** Start recurring liveness work for the current live socket. */
  startLiveness(
    probeIntervalMs: number,
    onProbe: () => void,
    freshnessIntervalMs: number,
    onFreshness: () => void,
  ): void {
    this.stopLiveness();
    this.probeTimer = setInterval(onProbe, probeIntervalMs);
    this.freshnessTimer = setInterval(onFreshness, freshnessIntervalMs);
  }

  /** Stop recurring liveness work without disturbing a retry deadline. */
  stopLiveness(): void {
    if (this.probeTimer !== undefined) {
      clearInterval(this.probeTimer);
      this.probeTimer = undefined;
    }
    if (this.freshnessTimer !== undefined) {
      clearInterval(this.freshnessTimer);
      this.freshnessTimer = undefined;
    }
  }

  /** Replace the pending reconnect deadline with a new bounded delay. */
  startRetry(delayMs: number, onRetry: () => void): void {
    if (this.retryTimer !== undefined) {
      clearTimeout(this.retryTimer);
    }
    this.retryTimer = setTimeout(() => {
      this.retryTimer = undefined;
      onRetry();
    }, delayMs);
  }

  /** Stop every timer owned by the transport. */
  clear(): void {
    this.stopLiveness();
    this.clearWelcome();
    if (this.retryTimer !== undefined) {
      clearTimeout(this.retryTimer);
      this.retryTimer = undefined;
    }
  }
}
