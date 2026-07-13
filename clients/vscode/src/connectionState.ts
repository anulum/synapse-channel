// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — fail-closed editor connection state

/** Pure protocol negotiation and freshness transitions for editor clients. */

import { EDITOR_WIRE_PROTOCOL_VERSION } from "./hubProtocol.js";

/** Version assumed for hubs predating handshake version advertisement. */
export const FALLBACK_WIRE_PROTOCOL_VERSION = 1;

/** Oldest effective protocol that supports claim and release mutations. */
export const MINIMUM_MUTATION_PROTOCOL_VERSION = 1;

/** Liveness probes use the same cadence as the participant heartbeat. */
export const HUB_PROBE_INTERVAL_MS = 20_000;

/** Hub auth defaults to ten seconds; the editor allows two seconds of transport headroom. */
export const HUB_WELCOME_TIMEOUT_MS = 12_000;

/** Three missed probes plus scheduling headroom mark last-good state stale. */
export const HUB_STALE_AFTER_MS = 65_000;

/** A longer stale interval forces a new transport rather than waiting forever. */
export const HUB_RECONNECT_AFTER_MS = 120_000;

/** Frequency of the local freshness check. */
export const HUB_FRESHNESS_CHECK_MS = 5_000;

/** Connection phases exposed to status and mutation guards. */
export type HubConnectionPhase =
  | "disconnected"
  | "negotiating"
  | "live"
  | "stale"
  | "incompatible"
  | "identity-mismatch";

/** Editor-visible connection state without raw peer diagnostics. */
export interface HubConnectionState {
  phase: HubConnectionPhase;
  peerProtocolVersion: number | null;
  effectiveProtocolVersion: number | null;
  warning: string | undefined;
  lastFrameAt: number | undefined;
}

/** Protocol comparison result matching the Python hub's negotiate-down rule. */
export interface ProtocolNegotiation {
  peerVersion: number | null;
  effectiveVersion: number;
  warning: string | undefined;
}

/** Initial state before a hub is configured. */
export function disconnectedConnection(): HubConnectionState {
  return {
    phase: "disconnected",
    peerProtocolVersion: null,
    effectiveProtocolVersion: null,
    warning: undefined,
    lastFrameAt: undefined,
  };
}

/** Negotiate to the lowest common wire version, warning on every skew. */
export function negotiateProtocol(peerVersion: number | null): ProtocolNegotiation {
  if (peerVersion === null) {
    return {
      peerVersion: null,
      effectiveVersion: Math.min(
        EDITOR_WIRE_PROTOCOL_VERSION,
        FALLBACK_WIRE_PROTOCOL_VERSION,
      ),
      warning:
        "Hub did not advertise a usable wire protocol version; compatibility version 1 is active.",
    };
  }
  const effectiveVersion = Math.min(EDITOR_WIRE_PROTOCOL_VERSION, peerVersion);
  if (peerVersion === EDITOR_WIRE_PROTOCOL_VERSION) {
    return { peerVersion, effectiveVersion, warning: undefined };
  }
  const direction = peerVersion < EDITOR_WIRE_PROTOCOL_VERSION ? "older" : "newer";
  return {
    peerVersion,
    effectiveVersion,
    warning:
      `Hub wire protocol ${peerVersion} is ${direction} than editor protocol `
      + `${EDITOR_WIRE_PROTOCOL_VERSION}; compatibility version ${effectiveVersion} is active.`,
  };
}

/** Preserve last-good evidence while beginning a new authenticated handshake. */
export function beginNegotiation(previous: HubConnectionState): HubConnectionState {
  return {
    phase: "negotiating",
    peerProtocolVersion: null,
    effectiveProtocolVersion: null,
    warning: undefined,
    lastFrameAt: previous.lastFrameAt,
  };
}

/** Accept a welcome frame or enter an incompatible fail-closed state. */
export function acceptWelcome(
  previous: HubConnectionState,
  peerVersion: number | null,
  observedAt: number,
): HubConnectionState {
  const negotiation = negotiateProtocol(peerVersion);
  if (negotiation.effectiveVersion < MINIMUM_MUTATION_PROTOCOL_VERSION) {
    return {
      phase: "incompatible",
      peerProtocolVersion: negotiation.peerVersion,
      effectiveProtocolVersion: negotiation.effectiveVersion,
      warning: "Hub protocol is too old for editor mutations.",
      lastFrameAt: previous.lastFrameAt,
    };
  }
  return {
    phase: "live",
    peerProtocolVersion: negotiation.peerVersion,
    effectiveProtocolVersion: negotiation.effectiveVersion,
    warning: negotiation.warning,
    lastFrameAt: observedAt,
  };
}

/** Record a validated post-welcome frame and restore a stale connection to live. */
export function observeHubFrame(
  state: HubConnectionState,
  observedAt: number,
): HubConnectionState {
  if (state.phase !== "live" && state.phase !== "stale") {
    return state;
  }
  return { ...state, phase: "live", lastFrameAt: observedAt };
}

/** Project the current time onto the connection freshness state. */
export function refreshConnectionFreshness(
  state: HubConnectionState,
  now: number,
): HubConnectionState {
  if (state.phase !== "live" || state.lastFrameAt === undefined) {
    return state;
  }
  return now - state.lastFrameAt > HUB_STALE_AFTER_MS
    ? { ...state, phase: "stale" }
    : state;
}

/** Preserve last-good state while marking the transport unavailable. */
export function markDisconnected(
  state: HubConnectionState,
  warning?: string,
): HubConnectionState {
  return {
    ...state,
    phase: "disconnected",
    effectiveProtocolVersion: null,
    warning,
  };
}

/** Stop automatic retries after a strict decoder rejects a known wire shape. */
export function markProtocolIncompatible(state: HubConnectionState): HubConnectionState {
  return {
    ...state,
    phase: "incompatible",
    effectiveProtocolVersion: null,
    warning: "Hub sent a frame that violates the editor wire contract.",
  };
}

/** Stop automatic retries when the hub rejects this identity's trust binding. */
export function markIdentityMismatch(state: HubConnectionState): HubConnectionState {
  return {
    ...state,
    phase: "identity-mismatch",
    effectiveProtocolVersion: null,
    warning: "Hub identity trust does not match this editor seat.",
  };
}

/** Whether an authoritative live handshake permits a version-one mutation. */
export function mutationsAllowed(state: HubConnectionState): boolean {
  return state.phase === "live"
    && state.effectiveProtocolVersion !== null
    && state.effectiveProtocolVersion >= MINIMUM_MUTATION_PROTOCOL_VERSION;
}

/** Age of the last validated hub frame, or undefined before the first one. */
export function lastFrameAgeMs(
  state: HubConnectionState,
  now: number,
): number | undefined {
  return state.lastFrameAt === undefined ? undefined : Math.max(0, now - state.lastFrameAt);
}
