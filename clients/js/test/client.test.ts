// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — tests for the JS/TS WebSocket client

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type ClaimScopeIdentity, MessageType, buildEnvelope } from "../src/protocol.js";
import { SynapseClient, type WebSocketLike } from "../src/client.js";

class FakeSocket implements WebSocketLike {
  sent: string[] = [];
  closed = false;
  onopen: ((event: unknown) => void) | null = null;
  onclose: ((event: unknown) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;

  send(data: string): void {
    this.sent.push(data);
  }
  close(): void {
    this.closed = true;
    this.onclose?.({});
  }
  open(): void {
    this.onopen?.({});
  }
  deliver(message: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
  welcome(): void {
    this.deliver({ type: MessageType.Welcome, sender: "hub" });
  }
  sentEnvelopes(): Record<string, unknown>[] {
    return this.sent.map((raw) => JSON.parse(raw) as Record<string, unknown>);
  }
}

function makeClient(extra: Record<string, unknown> = {}): { client: SynapseClient; socket: FakeSocket } {
  const socket = new FakeSocket();
  const client = new SynapseClient({
    uri: "ws://localhost:8876",
    name: "P/alice",
    webSocketFactory: () => socket,
    ...extra,
  });
  return { client, socket };
}

describe("buildEnvelope", () => {
  it("sets the base fields and merges extras", () => {
    const envelope = buildEnvelope("P/alice", MessageType.Claim, {
      now: 1,
      extra: { task_id: "t", paths: ["src/a"] },
    });
    expect(envelope).toMatchObject({
      sender: "P/alice",
      target: "all",
      type: "claim",
      payload: "",
      timestamp: 1,
      task_id: "t",
      paths: ["src/a"],
    });
  });
});

describe("SynapseClient connect", () => {
  it("registers with a token and resolves on welcome", async () => {
    const { client, socket } = makeClient({ token: "secret", takeover: true });
    const connected = client.connect();
    socket.open();
    socket.welcome();
    await expect(connected).resolves.toBeUndefined();
    expect(client.isReady).toBe(true);

    const registration = socket.sentEnvelopes()[0];
    expect(registration).toMatchObject({
      type: "heartbeat",
      target: "System",
      sender: "P/alice",
      token: "secret",
      takeover: true,
    });
  });

  it("rejects when the socket closes before a welcome", async () => {
    const { client, socket } = makeClient();
    const connected = client.connect();
    socket.open();
    socket.close();
    await expect(connected).rejects.toThrow(/closed the connection/);
  });

  it("rejects on a socket error before welcome", async () => {
    const { client, socket } = makeClient();
    const connected = client.connect();
    socket.onerror?.({});
    await expect(connected).rejects.toThrow(/failed/);
  });
});

describe("SynapseClient messaging", () => {
  it("sends typed chat, claim, and release envelopes", async () => {
    const { client, socket } = makeClient();
    const connected = client.connect();
    socket.open();
    socket.welcome();
    await connected;
    socket.sent = [];

    client.chat("hello", { target: "P/bob", priority: true });
    client.chat("secret", { channel: "ops" });
    const pathIdentity: ClaimScopeIdentity = {
      version: 1,
      worktree_path: "/repo",
      worktree_object_id: "1:2",
      filesystem_namespace: "host:1",
      case_sensitive: true,
      paths: [{ git_path: "src/a.ts", filesystem_path: "src/a.ts", object_id: "1:3" }],
    };
    client.claim("t1", ["src/a.ts"], pathIdentity);
    client.release("t1");
    client.requestBoard();

    const envelopes = socket.sentEnvelopes();
    expect(envelopes[0]).toMatchObject({ type: "chat", target: "P/bob", payload: "hello", priority: true });
    expect(envelopes[1]).toMatchObject({ type: "chat", channel: "ops", payload: "secret" });
    expect(envelopes[2]).toMatchObject({
      type: "claim",
      task_id: "t1",
      worktree: "/repo",
      paths: ["src/a.ts"],
      path_identity: pathIdentity,
    });
    expect(envelopes[3]).toMatchObject({ type: "release", task_id: "t1" });
    expect(envelopes[4]).toMatchObject({ type: "board_request" });
  });

  it("dispatches inbound messages by type and to any-handlers", async () => {
    const { client, socket } = makeClient();
    const connected = client.connect();
    socket.open();
    socket.welcome();
    await connected;

    const chats: string[] = [];
    const all: string[] = [];
    const unsubscribe = client.on(MessageType.Chat, (m) => chats.push(String(m["payload"])));
    client.onMessage((m) => all.push(m.type));

    socket.deliver({ type: "chat", sender: "P/bob", payload: "hi" });
    socket.deliver({ type: "presence_update", sender: "hub" });
    unsubscribe();
    socket.deliver({ type: "chat", sender: "P/bob", payload: "after-unsub" });

    expect(chats).toEqual(["hi"]);
    expect(all).toEqual(["chat", "presence_update", "chat"]);
  });

  it("ignores malformed inbound frames", async () => {
    const { client, socket } = makeClient();
    const connected = client.connect();
    socket.open();
    socket.welcome();
    await connected;

    const seen: string[] = [];
    client.onMessage((m) => seen.push(m.type));
    socket.onmessage?.({ data: "not json" });
    socket.onmessage?.({ data: 42 });
    socket.onmessage?.({ data: JSON.stringify({ no: "type" }) });
    expect(seen).toEqual([]);
  });

  it("throws when sending before connect", () => {
    const { client } = makeClient();
    expect(() => client.chat("x")).toThrow(/not connected/);
  });
});

describe("SynapseClient lifecycle", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("sends keepalive heartbeats and stops on close", async () => {
    const { client, socket } = makeClient({ heartbeatIntervalMs: 1000 });
    const connected = client.connect();
    socket.open();
    socket.welcome();
    await connected;
    socket.sent = [];

    vi.advanceTimersByTime(2500);
    const heartbeats = socket.sentEnvelopes().filter((e) => e["type"] === "heartbeat");
    expect(heartbeats.length).toBe(2);

    client.close();
    socket.sent = [];
    vi.advanceTimersByTime(5000);
    expect(socket.sent.length).toBe(0);
  });

  it("rejects connect when no welcome arrives before the timeout", async () => {
    const { client, socket } = makeClient({ readyTimeoutMs: 1000 });
    const connected = client.connect();
    socket.open();
    vi.advanceTimersByTime(1500);
    await expect(connected).rejects.toThrow(/did not welcome/);
  });
});
