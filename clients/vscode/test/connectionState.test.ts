// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — tests for fail-closed editor connection state

import { describe, expect, it } from "vitest";
import {
  HUB_STALE_AFTER_MS,
  acceptWelcome,
  beginNegotiation,
  disconnectedConnection,
  lastFrameAgeMs,
  markDisconnected,
  markIdentityMismatch,
  markProtocolIncompatible,
  mutationsAllowed,
  negotiateProtocol,
  observeHubFrame,
  refreshConnectionFreshness,
  type HubConnectionState,
} from "../src/connectionState.js";

function liveConnection(observedAt: number = 1_000): HubConnectionState {
  return acceptWelcome(disconnectedConnection(), 2, observedAt);
}

describe("negotiateProtocol", () => {
  it("uses version two without a warning for the matching real hub contract", () => {
    expect(negotiateProtocol(2)).toEqual({
      peerVersion: 2,
      effectiveVersion: 2,
      warning: undefined,
    });
  });

  it("uses the documented version-one fallback when the field is absent", () => {
    expect(negotiateProtocol(null)).toEqual({
      peerVersion: null,
      effectiveVersion: 1,
      warning:
        "Hub did not advertise a usable wire protocol version; compatibility version 1 is active.",
    });
  });

  it("negotiates down and names older and newer skew", () => {
    const older = negotiateProtocol(1);
    const newer = negotiateProtocol(5);
    expect(older.effectiveVersion).toBe(1);
    expect(older.warning).toContain("older");
    expect(newer.effectiveVersion).toBe(2);
    expect(newer.warning).toContain("newer");
  });
});

describe("connection transitions", () => {
  it("moves through negotiation to live and permits mutations only after welcome", () => {
    const initial = disconnectedConnection();
    const negotiating = beginNegotiation(initial);
    const live = acceptWelcome(negotiating, 2, 4_000);
    expect(negotiating.phase).toBe("negotiating");
    expect(mutationsAllowed(negotiating)).toBe(false);
    expect(live.phase).toBe("live");
    expect(live.lastFrameAt).toBe(4_000);
    expect(mutationsAllowed(live)).toBe(true);
  });

  it("refuses a protocol below the mutation floor", () => {
    const state = acceptWelcome(disconnectedConnection(), 0, 1_000);
    expect(state.phase).toBe("incompatible");
    expect(state.effectiveProtocolVersion).toBe(0);
    expect(mutationsAllowed(state)).toBe(false);
  });

  it("marks last-good state stale only after three missed probes plus headroom", () => {
    const live = liveConnection(10_000);
    expect(refreshConnectionFreshness(live, 10_000 + HUB_STALE_AFTER_MS)).toBe(live);
    const stale = refreshConnectionFreshness(live, 10_001 + HUB_STALE_AFTER_MS);
    expect(stale.phase).toBe("stale");
    expect(stale.lastFrameAt).toBe(10_000);
    expect(mutationsAllowed(stale)).toBe(false);
  });

  it("leaves non-live and pre-observation states unchanged during freshness checks", () => {
    const disconnected = disconnectedConnection();
    const liveWithoutObservation = { ...disconnected, phase: "live" as const };
    expect(refreshConnectionFreshness(disconnected, 100_000)).toBe(disconnected);
    expect(refreshConnectionFreshness(liveWithoutObservation, 100_000)).toBe(liveWithoutObservation);
  });

  it("restores stale state to live after a validated frame", () => {
    const stale = refreshConnectionFreshness(
      liveConnection(1_000),
      1_001 + HUB_STALE_AFTER_MS,
    );
    const restored = observeHubFrame(stale, 90_000);
    expect(restored.phase).toBe("live");
    expect(restored.lastFrameAt).toBe(90_000);
    expect(mutationsAllowed(restored)).toBe(true);
  });

  it("does not let a pre-welcome frame authorise mutations", () => {
    const negotiating = beginNegotiation(disconnectedConnection());
    expect(observeHubFrame(negotiating, 5_000)).toBe(negotiating);
  });

  it("retains last-frame evidence across disconnect and renegotiation", () => {
    const live = liveConnection(7_500);
    const disconnected = markDisconnected(live, "Hub authentication was refused.");
    const negotiating = beginNegotiation(disconnected);
    expect(disconnected.lastFrameAt).toBe(7_500);
    expect(disconnected.warning).toBe("Hub authentication was refused.");
    expect(negotiating.lastFrameAt).toBe(7_500);
    expect(mutationsAllowed(disconnected)).toBe(false);
  });

  it("keeps identity and wire-contract failures distinct", () => {
    const live = liveConnection();
    const identity = markIdentityMismatch(live);
    const protocol = markProtocolIncompatible(live);
    expect(identity.phase).toBe("identity-mismatch");
    expect(identity.warning).toContain("identity trust");
    expect(protocol.phase).toBe("incompatible");
    expect(protocol.warning).toContain("wire contract");
    expect(mutationsAllowed(identity)).toBe(false);
    expect(mutationsAllowed(protocol)).toBe(false);
  });

  it("reports a non-negative last-frame age", () => {
    const state = liveConnection(2_000);
    expect(lastFrameAgeMs(state, 3_250)).toBe(1_250);
    expect(lastFrameAgeMs(state, 1_500)).toBe(0);
    expect(lastFrameAgeMs(disconnectedConnection(), 3_000)).toBeUndefined();
  });
});
