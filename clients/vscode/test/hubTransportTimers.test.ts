// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — tests for editor transport timer ownership

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HubTransportTimers } from "../src/hubTransportTimers.js";

describe("HubTransportTimers", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("replaces and clears welcome deadlines", () => {
    const timers = new HubTransportTimers();
    const first = vi.fn();
    const second = vi.fn();
    timers.startWelcome(10, first);
    timers.startWelcome(20, second);
    vi.advanceTimersByTime(20);
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledOnce();
    timers.clearWelcome();
  });

  it("owns recurring liveness intervals and replaces the prior pair", () => {
    const timers = new HubTransportTimers();
    const oldProbe = vi.fn();
    const probe = vi.fn();
    const freshness = vi.fn();
    timers.startLiveness(5, oldProbe, 7, vi.fn());
    timers.startLiveness(10, probe, 15, freshness);
    vi.advanceTimersByTime(30);
    expect(oldProbe).not.toHaveBeenCalled();
    expect(probe).toHaveBeenCalledTimes(3);
    expect(freshness).toHaveBeenCalledTimes(2);
    timers.stopLiveness();
    timers.stopLiveness();
  });

  it("replaces retry work and clear cancels every remaining deadline", () => {
    const timers = new HubTransportTimers();
    const first = vi.fn();
    const second = vi.fn();
    timers.startRetry(10, first);
    timers.startRetry(20, second);
    vi.advanceTimersByTime(20);
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledOnce();
    timers.startRetry(10, first);
    timers.startWelcome(10, first);
    timers.startLiveness(10, first, 10, first);
    timers.clear();
    vi.advanceTimersByTime(20);
    expect(first).not.toHaveBeenCalled();
  });
});
