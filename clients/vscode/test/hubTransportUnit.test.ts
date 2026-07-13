// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — deterministic fail-closed transport branch tests

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  HUB_PROBE_INTERVAL_MS,
  HUB_RECONNECT_AFTER_MS,
  HUB_WELCOME_TIMEOUT_MS,
  type HubConnectionState,
} from "../src/connectionState.js";
import { type HubFrame } from "../src/hubProtocol.js";
import { HubTransport } from "../src/hubTransport.js";

type FakeEvent = Record<string, unknown>;
type FakeListener = (event: FakeEvent) => void;

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static instances: FakeWebSocket[] = [];
  static throwNext = false;

  readyState = FakeWebSocket.CONNECTING;
  readonly sent: string[] = [];
  readonly closes: Array<{ code: number; reason: string }> = [];
  private readonly listeners = new Map<string, FakeListener[]>();

  constructor(readonly uri: string) {
    if (FakeWebSocket.throwNext) {
      FakeWebSocket.throwNext = false;
      throw new Error("constructor refusal");
    }
    FakeWebSocket.instances.push(this);
  }

  addEventListener(type: string, listener: FakeListener): void {
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  send(payload: string): void {
    this.sent.push(payload);
  }

  close(code = 1000, reason = ""): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.closes.push({ code, reason });
    this.emit("close", { code, reason });
  }

  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.emit("open");
  }

  message(frame: unknown): void {
    const data = typeof frame === "string" ? frame : JSON.stringify(frame);
    this.emit("message", { data });
  }

  fail(): void {
    this.emit("error");
  }

  emit(type: string, event: FakeEvent = {}): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }
}

function transportFixture(): {
  transport: HubTransport;
  states: HubConnectionState[];
  frames: HubFrame[];
} {
  const states: HubConnectionState[] = [];
  const frames: HubFrame[] = [];
  return {
    transport: new HubTransport({
      onConnectionState: (state) => states.push(state),
      onFrame: (frame) => frames.push(frame),
    }),
    states,
    frames,
  };
}

function currentSocket(): FakeWebSocket {
  const socket = FakeWebSocket.instances.at(-1);
  if (socket === undefined) {
    throw new Error("Expected a fake WebSocket instance.");
  }
  return socket;
}

function welcome(socket: FakeWebSocket, protocolVersion = 2): void {
  socket.open();
  socket.message({ type: "welcome", online_agents: [], protocol_version: protocolVersion });
}

describe("HubTransport deterministic boundaries", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    FakeWebSocket.throwNext = false;
    vi.useFakeTimers();
    vi.setSystemTime(1_000);
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.spyOn(Math, "random").mockReturnValue(0);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("negotiates, sends bounded reads and mutations, and probes liveness", () => {
    const { transport, frames } = transportFixture();
    transport.connect("ws://fixture", "editor/seat", "token");
    expect(transport.request("state_request")).toBe(false);
    const socket = currentSocket();
    welcome(socket);
    expect(JSON.parse(socket.sent[0] ?? "{}")).toMatchObject({
      type: "heartbeat",
      sender: "editor/seat",
      token: "token",
    });
    expect(transport.request("board_request")).toBe(true);
    expect(transport.mutate("claim", { task_id: "T1" })).toEqual({ sent: true });
    socket.message({ type: "future_additive" });
    expect(frames.at(-1)).toEqual({ kind: "ignored", wireType: "future_additive" });
    vi.advanceTimersByTime(HUB_PROBE_INTERVAL_MS);
    expect(socket.sent.some((payload) => JSON.parse(payload).type === "who_request")).toBe(true);

    socket.readyState = FakeWebSocket.CONNECTING;
    expect(transport.mutate("release", { task_id: "T1" })).toEqual({
      sent: false,
      reason: "SYNAPSE mutation withheld because the live transport is unavailable.",
    });
    transport.dispose();
  });

  it("rejects malformed and unsupported welcome frames without retrying", () => {
    const malformed = transportFixture();
    malformed.transport.connect("ws://malformed", "editor/seat");
    const malformedSocket = currentSocket();
    malformedSocket.open();
    malformedSocket.message("not-json");
    expect(malformed.transport.state().phase).toBe("incompatible");
    expect(malformedSocket.closes).toContainEqual({ code: 4002, reason: "wire contract violation" });

    const oldProtocol = transportFixture();
    oldProtocol.transport.connect("ws://old", "editor/seat");
    const oldSocket = currentSocket();
    welcome(oldSocket, 0);
    expect(oldProtocol.transport.state().phase).toBe("incompatible");
    expect(oldSocket.closes).toContainEqual({ code: 4002, reason: "unsupported wire protocol" });
  });

  it("stops retries on identity and authentication refusal until reconfigured", () => {
    const identity = transportFixture();
    identity.transport.connect("ws://identity", "editor/seat");
    const identitySocket = currentSocket();
    welcome(identitySocket);
    identitySocket.close(4013, "identity pin mismatch private");
    expect(identity.transport.state().phase).toBe("identity-mismatch");

    const auth = transportFixture();
    auth.transport.connect("ws://auth", "editor/seat");
    const authSocket = currentSocket();
    welcome(authSocket);
    authSocket.close(4010, "private peer reason");
    expect(auth.transport.state()).toMatchObject({
      phase: "disconnected",
      warning: "Hub authentication or seat ownership was refused.",
    });
    vi.advanceTimersByTime(250);
    expect(FakeWebSocket.instances).toHaveLength(2);
    auth.transport.connect("ws://auth", "editor/seat", "replacement-token");
    expect(FakeWebSocket.instances).toHaveLength(3);
  });

  it("retries constructor and asynchronous connection failures", () => {
    FakeWebSocket.throwNext = true;
    const constructorFailure = transportFixture();
    constructorFailure.transport.connect("ws://throws", "editor/seat");
    expect(constructorFailure.transport.state().warning).toBe("Hub transport could not be opened.");
    vi.advanceTimersByTime(250);
    expect(FakeWebSocket.instances).toHaveLength(1);

    const asynchronous = transportFixture();
    asynchronous.transport.connect("ws://fails", "editor/seat");
    currentSocket().fail();
    expect(asynchronous.transport.state().phase).toBe("disconnected");
    vi.advanceTimersByTime(250);
    expect(FakeWebSocket.instances).toHaveLength(3);
  });

  it("bounds a missing welcome and ignores events from a replaced socket", () => {
    const { transport, states } = transportFixture();
    transport.connect("ws://first", "editor/seat");
    const first = currentSocket();
    welcome(first);
    expect(transport.state().lastFrameAt).toBe(1_000);
    transport.connect("ws://second", "editor/seat");
    const second = currentSocket();
    expect(transport.state().lastFrameAt).toBeUndefined();
    first.open();
    first.message({ type: "welcome", online_agents: [], protocol_version: 2 });
    first.fail();
    first.emit("close", { code: 1006, reason: "late" });
    expect(transport.state().phase).toBe("negotiating");
    second.open();
    vi.advanceTimersByTime(HUB_WELCOME_TIMEOUT_MS);
    expect(second.closes).toContainEqual({ code: 4000, reason: "welcome timeout" });
    expect(states.some((state) => state.phase === "disconnected")).toBe(true);
  });

  it("marks a silent live connection stale and recycles it after the hard deadline", () => {
    const { transport } = transportFixture();
    transport.connect("ws://stale", "editor/seat");
    const socket = currentSocket();
    welcome(socket);
    vi.advanceTimersByTime(HUB_RECONNECT_AFTER_MS + HUB_PROBE_INTERVAL_MS);
    expect(socket.closes).toContainEqual({ code: 4000, reason: "stale transport" });
    expect(transport.state().phase).toBe("negotiating");
    expect(FakeWebSocket.instances.length).toBeGreaterThan(1);
  });
});
