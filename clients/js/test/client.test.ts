// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — tests for the JS/TS WebSocket client

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type ClaimScopeIdentity, type Envelope, MessageType, buildEnvelope } from "../src/protocol.js";
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
  it("rejects promptly when an identity signer fails", async () => {
    const { client, socket } = makeClient({
      signRegistration: () => { throw new Error("signer unavailable"); },
    });
    const pending = client.connect();
    socket.open();
    await expect(pending).rejects.toThrow(/signer unavailable/);
    expect(socket.closed).toBe(true);
  });

  it("gates attachments on wire v4 and signs both registration and requests", async () => {
    const { client, socket } = makeClient({
      signRegistration: (frame: Envelope) => ({ ...frame, signature: { test: true } }),
      signAttachment: (frame: Envelope) => ({ ...frame, auth: { test: true } }),
    });
    const connected = client.connect();
    socket.open();
    expect(socket.sentEnvelopes()[0]).toHaveProperty("signature");
    socket.deliver({ type: MessageType.Welcome, sender: "SynapseHub", protocol_version: 3 });
    await connected;
    expect(() => client.attachment(MessageType.AttachmentInfo, { scope: "P", digest: "a" })).toThrow(/version four/);
    client.close();

    const second = makeClient({
      signAttachment: (frame: Envelope) => ({ ...frame, auth: { test: true } }),
    });
    const ready = second.client.connect();
    second.socket.open();
    second.socket.deliver({ type: MessageType.Welcome, sender: "SynapseHub", protocol_version: 4 });
    await ready;
    second.client.attachment(MessageType.AttachmentInfo, { scope: "P", digest: "a" });
    expect(second.socket.sentEnvelopes().at(-1)).toMatchObject({
      type: "attachment_info", scope: "P", digest: "a", auth: { test: true },
    });
    expect(() => second.client.attachment("chat", {})).toThrow(/unknown attachment/);
    second.client.close();
  });

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
      paths: [{
        git_path: "src/a.ts/.synapse-symbol/Worker/run",
        filesystem_path: "src/a.ts/.synapse-symbol/Worker/run",
        object_id: "1:3",
        object_scope: "Worker/run",
      }],
    };
    client.claim("t1", ["src/a.ts/.synapse-symbol/Worker/run"], pathIdentity);
    client.release("t1");
    client.requestBoard();
    client.requestWho();
    client.requestWho("laptop");
    client.claim("lock", [], undefined, { taskOnly: true });
    expect(() => client.claim("loose")).toThrow(/taskOnly/);
    expect(() => client.claim("mixed", ["a.ts"], undefined, { taskOnly: true })).toThrow(
      /cannot be combined/,
    );

    const envelopes = socket.sentEnvelopes();
    expect(envelopes.at(-3)).toMatchObject({ type: "who_request" });
    expect(envelopes.at(-3)).not.toHaveProperty("hub");
    expect(envelopes.at(-2)).toMatchObject({ type: "who_request", hub: "laptop" });
    expect(envelopes.at(-1)).toMatchObject({
      type: "claim",
      task_id: "lock",
      paths: [],
      worktree: "lock",
    });
    expect(envelopes[0]).toMatchObject({ type: "chat", target: "P/bob", payload: "hello", priority: true });
    expect(envelopes[1]).toMatchObject({ type: "chat", channel: "ops", payload: "secret" });
    expect(envelopes[2]).toMatchObject({
      type: "claim",
      task_id: "t1",
      worktree: "/repo",
      paths: ["src/a.ts/.synapse-symbol/Worker/run"],
      path_identity: pathIdentity,
    });
    expect(envelopes[3]).toMatchObject({ type: "release", task_id: "t1" });
    expect(envelopes[4]).toMatchObject({ type: "board_request" });
  });

  it("names the fencing epoch of its own grant when releasing", async () => {
    const { client, socket } = makeClient();
    const connected = client.connect();
    socket.open();
    socket.welcome();
    await connected;
    socket.sent = [];

    socket.deliver({ type: MessageType.ClaimGranted, task_id: "t1", owner: "P/alice", epoch: 4 });
    // malformed or foreign grants change nothing
    socket.deliver({ type: MessageType.ClaimGranted, task_id: "t1", owner: "P/bob", epoch: 9 });
    socket.deliver({ type: MessageType.ClaimGranted, task_id: "t1", owner: "P/alice", epoch: "9" });
    socket.deliver({ type: MessageType.ClaimGranted, task_id: "t1", owner: "P/alice", epoch: -1 });
    socket.deliver({ type: MessageType.ClaimGranted, task_id: "t1", owner: "P/alice", epoch: 1.5 });
    socket.deliver({ type: MessageType.ClaimGranted, task_id: 7, owner: "P/alice", epoch: 9 });
    expect(client.leaseEpoch("t1")).toBe(4);
    client.release("t1");
    client.release("t1", 2); // an explicit epoch wins
    client.release("unknown");

    socket.deliver({ type: MessageType.HandoffGranted, task_id: "t2", owner: "P/alice", epoch: 6 });
    expect(client.leaseEpoch("t2")).toBe(6);
    socket.deliver({ type: MessageType.HandoffGranted, task_id: "t2", owner: "P/bob", epoch: 7 });
    expect(client.leaseEpoch("t2")).toBeUndefined(); // handed away, so forgotten
    socket.deliver({ type: MessageType.ReleaseGranted, task_id: "t1", owner: "P/alice" });
    expect(client.leaseEpoch("t1")).toBeUndefined(); // released, so forgotten
    client.release("t1");

    expect(socket.sentEnvelopes()).toMatchObject([
      { type: "release", task_id: "t1", epoch: 4 },
      { type: "release", task_id: "t1", epoch: 2 },
      { type: "release", task_id: "unknown" },
      { type: "release", task_id: "t1" },
    ]);
    expect(socket.sentEnvelopes()[2]).not.toHaveProperty("epoch");
    expect(socket.sentEnvelopes()[3]).not.toHaveProperty("epoch");
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

describe("SynapseClient reconnect contract", () => {
  function makeReconnectingClient(extra: Record<string, unknown> = {}): {
    client: SynapseClient;
    sockets: FakeSocket[];
  } {
    const sockets: FakeSocket[] = [];
    const client = new SynapseClient({
      uri: "ws://localhost:8876",
      name: "P/alice",
      webSocketFactory: () => {
        const socket = new FakeSocket();
        sockets.push(socket);
        return socket;
      },
      ...extra,
    });
    return { client, sockets };
  }

  it("is not ready after the hub closes and reconnects the same instance", async () => {
    const { client, sockets } = makeReconnectingClient();
    const welcomes: string[] = [];
    client.on(MessageType.Welcome, (message) => welcomes.push(String(message.sender)));
    const first = client.connect();
    sockets[0]!.open();
    sockets[0]!.welcome();
    await first;
    expect(client.isReady).toBe(true);

    sockets[0]!.onclose?.({});
    expect(client.isReady).toBe(false);
    expect(() => client.chat("x")).toThrow(/not connected/);

    const second = client.connect();
    expect(sockets.length).toBe(2);
    sockets[1]!.open();
    sockets[1]!.deliver({ type: MessageType.Welcome, sender: "hub-2" });
    await second;
    expect(client.isReady).toBe(true);
    expect(welcomes).toEqual(["hub", "hub-2"]);
    expect(sockets[1]!.sentEnvelopes()[0]).toMatchObject({ type: "heartbeat", payload: "online" });
  });

  it("rejects a second connect while a socket is open or pending", async () => {
    const { client, sockets } = makeReconnectingClient();
    const first = client.connect();
    await expect(client.connect()).rejects.toThrow(/already has an open or pending connection/);
    sockets[0]!.open();
    sockets[0]!.welcome();
    await first;
    await expect(client.connect()).rejects.toThrow(/close\(\) it first/);
    expect(sockets.length).toBe(1);
    expect(client.isReady).toBe(true);
  });

  it("close() rejects a pending connect and ignores the closed socket afterwards", async () => {
    const { client, sockets } = makeReconnectingClient();
    const dispatched: string[] = [];
    client.onMessage((message) => dispatched.push(message.type));
    const pending = client.connect();
    sockets[0]!.open();
    client.close();
    await expect(pending).rejects.toThrow(/closed before the hub welcomed it/);
    expect(sockets[0]!.closed).toBe(true);
    expect(client.isReady).toBe(false);

    sockets[0]!.welcome();
    sockets[0]!.deliver({ type: MessageType.Chat, sender: "P/bob", payload: "late" });
    expect(client.isReady).toBe(false);
    expect(dispatched).toEqual([]);

    const next = client.connect();
    sockets[1]!.open();
    sockets[1]!.welcome();
    await next;
    expect(client.isReady).toBe(true);
    expect(dispatched).toEqual([MessageType.Welcome]);
  });

  it("a welcome timeout frees the client for a later successful connect", async () => {
    vi.useFakeTimers();
    try {
      const { client, sockets } = makeReconnectingClient({ readyTimeoutMs: 1000 });
      const timedOut = client.connect();
      sockets[0]!.open();
      vi.advanceTimersByTime(1500);
      await expect(timedOut).rejects.toThrow(/did not welcome/);
      expect(sockets[0]!.closed).toBe(true);
      expect(client.isReady).toBe(false);
      sockets[0]!.welcome();
      expect(client.isReady).toBe(false);

      const next = client.connect();
      sockets[1]!.open();
      sockets[1]!.welcome();
      await next;
      expect(client.isReady).toBe(true);
      sockets[1]!.sent = [];
      vi.advanceTimersByTime(20_000);
      expect(sockets[1]!.sentEnvelopes().filter((e) => e["type"] === "heartbeat").length).toBe(1);
      expect(sockets[0]!.sent.length).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("an error before the welcome rejects once and leaves the client reusable", async () => {
    const { client, sockets } = makeReconnectingClient();
    const failed = client.connect();
    sockets[0]!.onerror?.({});
    await expect(failed).rejects.toThrow(/failed/);
    sockets[0]!.onclose?.({});
    expect(client.isReady).toBe(false);
    const next = client.connect();
    sockets[1]!.open();
    sockets[1]!.welcome();
    await next;
    expect(client.isReady).toBe(true);
  });
});


describe("SynapseClient asynchronous close boundary", () => {
  /** Build transports whose close request and close completion are independent. */
  function fixture(): { client: SynapseClient; sockets: FakeSocket[] } {
    const sockets: FakeSocket[] = [];
    const client = new SynapseClient({
      uri: "ws://localhost:8876", name: "P/alice",
      webSocketFactory: () => {
        const socket = new FakeSocket();
        socket.close = () => { socket.closed = true; };
        sockets.push(socket);
        return socket;
      },
    });
    return { client, sockets };
  }

  it("a signer failure requests one close and waits before reusing the identity", async () => {
    const sockets: FakeSocket[] = [];
    let closes = 0;
    const client = new SynapseClient({
      uri: "ws://localhost:8876", name: "P/alice",
      signRegistration: () => { throw new Error("signer unavailable"); },
      webSocketFactory: () => {
        const socket = new FakeSocket();
        socket.close = () => { closes += 1; socket.closed = true; };
        sockets.push(socket);
        return socket;
      },
    });
    const failed = client.connect();
    sockets[0]!.open();
    await expect(failed).rejects.toThrow(/signer unavailable/);
    expect(closes).toBe(1);
    const next = client.connect();
    expect(sockets.length).toBe(1);
    sockets[0]!.onclose?.({});
    await vi.waitFor(() => expect(sockets.length).toBe(2));
    sockets[1]!.open();
    await expect(next).rejects.toThrow(/signer unavailable/);
    expect(closes).toBe(2);
    sockets[1]!.onclose?.({});
  });
  it("a transport close failure rejects connect without opening a replacement", async () => {
    const { client, sockets } = fixture();
    const failed = client.connect();
    sockets[0]!.close = () => { throw new Error("close failed"); };
    sockets[0]!.onerror?.({});
    await expect(failed).rejects.toThrow(/close failed/);
    await expect(client.connect()).rejects.toThrow(/close failed/);
    expect(sockets.length).toBe(1);
    sockets[0]!.onclose?.({});
    const next = client.connect();
    sockets[1]!.open(); sockets[1]!.welcome(); await next;
    client.close(); sockets[1]!.onclose?.({});
  });
  it("does not reopen the name before the old close handshake completes", async () => {
    const { client, sockets } = fixture();
    const first = client.connect();
    sockets[0]!.open(); sockets[0]!.welcome(); await first;
    client.close();
    const next = client.connect();
    expect(sockets.length).toBe(1);
    await expect(client.connect()).rejects.toThrow(/already has an open or pending/);
    sockets[0]!.onclose?.({});
    await vi.waitFor(() => expect(sockets.length).toBe(2));
    sockets[1]!.open(); sockets[1]!.welcome(); await next;
    client.close(); sockets[1]!.onclose?.({});
  });
  it("a second close cancels the queued reconnect", async () => {
    const { client, sockets } = fixture();
    const first = client.connect();
    sockets[0]!.open(); sockets[0]!.welcome(); await first;
    client.close();
    const next = client.connect();
    client.close();
    sockets[0]!.onclose?.({});
    await expect(next).rejects.toThrow(/closed while awaiting/);
    expect(sockets.length).toBe(1);
    expect(client.isReady).toBe(false);
  });
  it("a close timeout refuses reconnect without taking over the name", async () => {
    vi.useFakeTimers();
    try {
      const { client, sockets } = fixture();
      const first = client.connect();
      sockets[0]!.open(); sockets[0]!.welcome(); await first;
      client.close();
      const next = client.connect();
      vi.advanceTimersByTime(5000);
      await expect(next).rejects.toThrow(/did not close in time/);
      expect(sockets.length).toBe(1);
      sockets[0]!.onclose?.({});
      const recovered = client.connect();
      sockets[1]!.open(); sockets[1]!.welcome(); await recovered;
      client.close(); sockets[1]!.onclose?.({});
    } finally { vi.useRealTimers(); }
  });
});
