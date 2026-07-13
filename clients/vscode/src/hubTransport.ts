// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — reconnecting editor WebSocket transport

/** Own the WebSocket, handshake, liveness probes, and bounded reconnect loop. */
import {
  HUB_FRESHNESS_CHECK_MS,
  HUB_PROBE_INTERVAL_MS,
  HUB_RECONNECT_AFTER_MS,
  HUB_WELCOME_TIMEOUT_MS,
  acceptWelcome,
  beginNegotiation,
  disconnectedConnection,
  lastFrameAgeMs,
  markDisconnected,
  markIdentityMismatch,
  markProtocolIncompatible,
  mutationsAllowed,
  observeHubFrame,
  refreshConnectionFreshness,
  type HubConnectionState,
} from "./connectionState.js";
import { registrationHeartbeat } from "./hubAuth.js";
import { decideHubClose } from "./hubClosePolicy.js";
import { decodeHubFrame, type HubFrame } from "./hubProtocol.js";
import { reconnectDelayMs } from "./reconnectPolicy.js";
import { HubTransportTimers } from "./hubTransportTimers.js";
import {
  type HubMutation,
  type HubReadRequest,
  type HubTransportEvents,
  type MutationSendResult,
} from "./hubTransportTypes.js";

interface HubConfiguration {
  uri: string;
  identity: string;
  token: string | undefined;
}
/** Reconnecting, protocol-aware transport for one configured hub and identity. */
export class HubTransport {
  private stateValue = disconnectedConnection();
  private configuration: HubConfiguration | undefined;
  private socket: WebSocket | undefined;
  private epoch = 0;
  private retryAttempt = 0;
  private readonly timers = new HubTransportTimers();

  constructor(private readonly events: HubTransportEvents) {}

  /** Return the latest immutable-by-convention connection projection. */
  state(): HubConnectionState {
    return this.stateValue;
  }

  /** Replace the complete connection configuration and begin a fresh handshake. */
  connect(uri: string, identity: string, token?: string): void {
    const targetChanged = this.configuration === undefined
      || this.configuration.uri !== uri
      || this.configuration.identity !== identity;
    this.epoch += 1;
    this.timers.clear();
    this.socket?.close();
    this.socket = undefined;
    this.retryAttempt = 0;
    this.configuration = { uri, identity, token };
    if (targetChanged) {
      this.stateValue = disconnectedConnection();
    }
    this.updateState(beginNegotiation(this.stateValue));
    this.open(this.epoch);
  }

  /** Send an authoritative read query only after the welcome handshake. */
  request(type: HubReadRequest): boolean {
    if (this.stateValue.phase !== "live" && this.stateValue.phase !== "stale") {
      return false;
    }
    return this.sendFrame({ type, sender: this.configuration?.identity ?? "" });
  }

  /** Send a mutation only while the negotiated state is live and compatible. */
  mutate(type: HubMutation, fields: Record<string, unknown>): MutationSendResult {
    if (!mutationsAllowed(this.stateValue)) {
      return {
        sent: false,
        reason: "SYNAPSE mutation withheld because the hub state is not live and compatible.",
      };
    }
    const identity = this.configuration?.identity;
    if (identity === undefined || !this.sendFrame({ type, sender: identity, ...fields })) {
      return {
        sent: false,
        reason: "SYNAPSE mutation withheld because the live transport is unavailable.",
      };
    }
    return { sent: true };
  }

  /** Stop all transport work without scheduling another connection. */
  dispose(): void {
    this.epoch += 1;
    this.configuration = undefined;
    this.timers.clear();
    this.socket?.close();
    this.socket = undefined;
    this.updateState(markDisconnected(this.stateValue));
  }

  private open(epoch: number): void {
    const configuration = this.configuration;
    if (configuration === undefined || epoch !== this.epoch) {
      return;
    }
    let socket: WebSocket;
    try {
      socket = new WebSocket(configuration.uri);
    } catch {
      this.updateState(markDisconnected(this.stateValue, "Hub transport could not be opened."));
      this.scheduleReconnect(epoch);
      return;
    }
    this.socket = socket;
    socket.addEventListener("open", () => {
      if (epoch !== this.epoch || socket !== this.socket) {
        return;
      }
      this.sendFrame(registrationHeartbeat(configuration.identity, configuration.token));
      this.timers.startWelcome(HUB_WELCOME_TIMEOUT_MS, () => {
        if (epoch === this.epoch && socket === this.socket
            && this.stateValue.phase === "negotiating") {
          socket.close(4000, "welcome timeout");
        }
      });
    });
    socket.addEventListener("message", (event: MessageEvent) => {
      if (epoch === this.epoch && socket === this.socket) {
        this.receive(String(event.data), socket, epoch);
      }
    });
    socket.addEventListener("error", () => {
      if (epoch === this.epoch && socket === this.socket) {
        this.socket = undefined;
        try { socket.close(); } catch { /* A failed connecting socket may reject close. */ }
        this.timers.clear();
        this.updateState(markDisconnected(this.stateValue, "Hub transport could not be opened."));
        this.scheduleReconnect(epoch);
      }
    });
    socket.addEventListener("close", (event: CloseEvent) => {
      if (epoch === this.epoch && socket === this.socket) {
        this.closed(event, epoch);
      }
    });
  }

  private receive(raw: string, socket: WebSocket, epoch: number): void {
    const decoded = decodeHubFrame(raw);
    if (!decoded.ok) {
      this.timers.clear();
      this.updateState(markProtocolIncompatible(this.stateValue));
      socket.close(4002, "wire contract violation");
      return;
    }
    const now = Date.now();
    if (decoded.frame.kind === "welcome") {
      this.timers.clearWelcome();
      this.updateState(
        acceptWelcome(this.stateValue, decoded.frame.peerProtocolVersion, now),
      );
      if (this.stateValue.phase === "incompatible") {
        socket.close(4002, "unsupported wire protocol");
        return;
      }
      this.retryAttempt = 0;
      this.startLiveness(epoch);
    } else {
      this.updateState(observeHubFrame(this.stateValue, now));
    }
    this.events.onFrame(decoded.frame);
  }

  private closed(event: CloseEvent, epoch: number): void {
    this.timers.clear();
    this.socket = undefined;
    if (this.stateValue.phase === "incompatible") {
      return;
    }
    const decision = decideHubClose(event);
    if (decision.kind === "identity-mismatch") {
      this.updateState(markIdentityMismatch(this.stateValue));
      return;
    }
    this.updateState(markDisconnected(this.stateValue, decision.warning));
    if (decision.kind === "terminal") {
      return;
    }
    this.scheduleReconnect(epoch);
  }

  private startLiveness(epoch: number): void {
    this.timers.startLiveness(HUB_PROBE_INTERVAL_MS, () => {
      if (epoch !== this.epoch) {
        return;
      }
      const identity = this.configuration?.identity;
      if (identity !== undefined) {
        this.sendFrame(registrationHeartbeat(identity));
        this.request("who_request");
        this.request("state_request");
      }
    }, HUB_FRESHNESS_CHECK_MS, () => {
      if (epoch !== this.epoch) {
        return;
      }
      const now = Date.now();
      this.updateState(refreshConnectionFreshness(this.stateValue, now));
      const age = lastFrameAgeMs(this.stateValue, now);
      if (age !== undefined && age > HUB_RECONNECT_AFTER_MS) {
        this.socket?.close(4000, "stale transport");
      }
    });
  }

  private scheduleReconnect(epoch: number): void {
    if (this.configuration === undefined || epoch !== this.epoch) {
      return;
    }
    const delay = reconnectDelayMs(this.retryAttempt, Math.random());
    this.retryAttempt += 1;
    this.timers.startRetry(delay, () => {
      if (epoch === this.epoch && this.configuration !== undefined) {
        this.updateState(beginNegotiation(this.stateValue));
        this.open(epoch);
      }
    });
  }

  private sendFrame(frame: Record<string, unknown>): boolean {
    const socket = this.socket;
    if (socket === undefined || socket.readyState !== WebSocket.OPEN) {
      return false;
    }
    socket.send(JSON.stringify(frame));
    return true;
  }

  private updateState(state: HubConnectionState): void {
    if (state === this.stateValue) {
      return;
    }
    this.stateValue = state;
    this.events.onConnectionState(state);
  }
}
