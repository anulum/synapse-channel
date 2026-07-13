// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — tests for bounded editor reconnect timing

import { describe, expect, it } from "vitest";
import {
  RECONNECT_BASE_DELAY_MS,
  RECONNECT_MAX_DELAY_MS,
  reconnectDelayMs,
} from "../src/reconnectPolicy.js";

describe("reconnectDelayMs", () => {
  it("uses equal jitter inside the first retry ceiling", () => {
    expect(reconnectDelayMs(0, 0)).toBe(RECONNECT_BASE_DELAY_MS / 2);
    expect(reconnectDelayMs(0, 0.5)).toBe(RECONNECT_BASE_DELAY_MS * 0.75);
    expect(reconnectDelayMs(0, 1)).toBe(RECONNECT_BASE_DELAY_MS);
  });

  it("doubles the ceiling before reaching the fixed maximum", () => {
    expect(reconnectDelayMs(1, 1)).toBe(1_000);
    expect(reconnectDelayMs(2, 1)).toBe(2_000);
    expect(reconnectDelayMs(5, 1)).toBe(16_000);
    expect(reconnectDelayMs(6, 1)).toBe(RECONNECT_MAX_DELAY_MS);
    expect(reconnectDelayMs(30, 1)).toBe(RECONNECT_MAX_DELAY_MS);
  });

  it("clamps invalid attempts and entropy without producing an unbounded delay", () => {
    expect(reconnectDelayMs(-4, -1)).toBe(RECONNECT_BASE_DELAY_MS / 2);
    expect(reconnectDelayMs(Number.NaN, Number.NaN)).toBe(RECONNECT_BASE_DELAY_MS / 2);
    expect(reconnectDelayMs(Number.POSITIVE_INFINITY, 2)).toBe(RECONNECT_BASE_DELAY_MS);
    expect(reconnectDelayMs(100, 1)).toBe(RECONNECT_MAX_DELAY_MS);
  });
});
