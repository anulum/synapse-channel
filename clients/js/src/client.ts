// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — typed WebSocket client for the coordination hub

import {
  type ClaimScopeIdentity,
  type Envelope,
  MessageType,
  buildEnvelope,
} from "./protocol.js";

/** A minimal structural view of the global `WebSocket`, for injection in tests. */
export interface WebSocketLike {
  send(data: string): void;
  close(): void;
  onopen: ((event: unknown) => void) | null;
  onclose: ((event: unknown) => void) | null;
  onerror: ((event: unknown) => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
}

/** Factory that opens a {@link WebSocketLike} for a URI; defaults to global `WebSocket`. */
export type WebSocketFactory = (uri: string) => WebSocketLike;

/** A handler invoked with each decoded inbound message. */
export type MessageHandler = (message: Envelope) => void;

/** Construction options for a {@link SynapseClient}. */
export interface SynapseClientOptions {
  /** Hub WebSocket URI, for example `ws://127.0.0.1:8876`. */
  uri: string;
  /** Stable agent identity bound on the registration frame. */
  name: string;
  /** Shared-secret token presented on the registration frame for a secured hub. */
  token?: string;
  /** Ask the hub to evict a stale holder of this name on connect. */
  takeover?: boolean;
  /** Keepalive heartbeat interval in milliseconds; defaults to 20000. */
  heartbeatIntervalMs?: number;
  /** Milliseconds to await the hub welcome before {@link connect} rejects; defaults to 5000. */
  readyTimeoutMs?: number;
  /** WebSocket factory override, for tests. Defaults to the global `WebSocket`. */
  webSocketFactory?: WebSocketFactory;
}

const MINIMUM_HEARTBEAT_MS = 1000;

function defaultFactory(uri: string): WebSocketLike {
  return new WebSocket(uri) as unknown as WebSocketLike;
}

/** One pending `connect()` attempt: its welcome timer and the promise it must settle. */
interface PendingAttempt {
  readonly timer: ReturnType<typeof setTimeout>;
  readonly reject: (error: Error) => void;
}

/**
 * A typed WebSocket client for the SYNAPSE CHANNEL hub.
 *
 * It registers an identity, keeps the connection alive with heartbeats, decodes
 * every inbound frame to a handler, and offers typed helpers for chat, claims,
 * releases, board reads, presence, and receipts. The same client runs in the
 * browser and in Node 20+ (both expose a global `WebSocket`).
 */
export class SynapseClient {
  private readonly options: SynapseClientOptions;
  private socket: WebSocketLike | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private readonly handlers = new Map<string, Set<MessageHandler>>();
  private readonly anyHandlers = new Set<MessageHandler>();
  private ready = false;
  /** Incremented on every connect and close; callbacks of an older socket are ignored. */
  private generation = 0;
  private pending: PendingAttempt | null = null;

  constructor(options: SynapseClientOptions) {
    this.options = options;
  }

  /** Whether the current socket has been welcomed by the hub; false once it closes. */
  get isReady(): boolean {
    return this.ready;
  }

  /**
   * Open the connection, register the identity, and resolve once the hub sends
   * its welcome. Rejects if the socket closes or errors before the welcome, if
   * the welcome does not arrive within `readyTimeoutMs`, or if `close()` is
   * called first.
   *
   * Each call is one socket generation: readiness starts false, timers and
   * handlers act only while that socket is current, and a closed socket leaves
   * the client not ready so the same instance can `connect()` again. A call
   * while a socket is already open or pending rejects instead of racing it;
   * `close()` first.
   */
  connect(): Promise<void> {
    if (this.socket !== null) {
      return Promise.reject(
        new Error(`${this.options.name} already has an open or pending connection; close() it first`),
      );
    }
    const factory = this.options.webSocketFactory ?? defaultFactory;
    const socket = factory(this.options.uri);
    const generation = ++this.generation;
    this.socket = socket;
    this.ready = false;
    return new Promise<void>((resolve, reject) => {
      const live = (): boolean => generation === this.generation && this.socket === socket;
      const fail = (error: Error): void => {
        this.generation += 1;
        this.pending = null;
        clearTimeout(timer);
        this.stopHeartbeat();
        this.ready = false;
        this.socket = null;
        reject(error);
      };
      const timer = setTimeout(() => {
        if (!live()) {
          return;
        }
        fail(new Error(`hub did not welcome ${this.options.name} in time`));
        socket.close();
      }, this.options.readyTimeoutMs ?? 5000);
      this.pending = { timer, reject };

      socket.onopen = () => {
        if (!live()) {
          return;
        }
        this.sendRegistration();
        this.startHeartbeat();
      };
      socket.onmessage = (event) => {
        if (!live()) {
          return;
        }
        const message = this.decode(event.data);
        if (message === null) {
          return;
        }
        if (!this.ready && message.type === MessageType.Welcome) {
          this.ready = true;
          this.pending = null;
          clearTimeout(timer);
          resolve();
        }
        this.dispatch(message);
      };
      socket.onerror = () => {
        if (!live() || this.ready) {
          return;
        }
        fail(new Error(`connection to ${this.options.uri} failed`));
      };
      socket.onclose = () => {
        if (!live()) {
          return;
        }
        const welcomed = this.ready;
        this.stopHeartbeat();
        this.ready = false;
        this.socket = null;
        this.pending = null;
        if (!welcomed) {
          clearTimeout(timer);
          reject(new Error(`hub closed the connection before welcoming ${this.options.name}`));
        }
      };
    });
  }

  /** Register a handler for one message type. Returns an unsubscribe function. */
  on(type: string, handler: MessageHandler): () => void {
    let set = this.handlers.get(type);
    if (set === undefined) {
      set = new Set();
      this.handlers.set(type, set);
    }
    set.add(handler);
    return () => set.delete(handler);
  }

  /** Register a handler for every inbound message. Returns an unsubscribe function. */
  onMessage(handler: MessageHandler): () => void {
    this.anyHandlers.add(handler);
    return () => this.anyHandlers.delete(handler);
  }

  /** Send a raw envelope of `type` with the given options. */
  send(type: string, options: { target?: string; payload?: string; extra?: Record<string, unknown> } = {}): void {
    if (this.socket === null) {
      throw new Error("client is not connected");
    }
    const envelope = buildEnvelope(this.options.name, type, options);
    this.socket.send(JSON.stringify(envelope));
  }

  /** Send a chat message to a target agent, `"all"`, or a private channel. */
  chat(payload: string, options: { target?: string; channel?: string; priority?: boolean } = {}): void {
    const extra: Record<string, unknown> = {};
    if (options.channel) {
      extra["channel"] = options.channel;
    }
    if (options.priority) {
      extra["priority"] = true;
    }
    this.send(MessageType.Chat, { target: options.target ?? "all", payload, extra });
  }

  /**
   * Claim a task, optionally scoped to display paths and a client-derived identity.
   *
   * The identity is an additive wire field. Callers that cannot derive it may
   * omit it and retain legacy literal-path comparison. Git-aware callers should
   * use the Python resolver rather than inventing canonical values.
   */
  claim(taskId: string, paths: string[] = [], pathIdentity?: ClaimScopeIdentity): void {
    const extra: Record<string, unknown> = { task_id: taskId, paths };
    if (pathIdentity !== undefined) {
      extra["worktree"] = pathIdentity.worktree_path;
      extra["path_identity"] = pathIdentity;
    }
    this.send(MessageType.Claim, { extra });
  }

  /** Release a claim you own. */
  release(taskId: string): void {
    this.send(MessageType.Release, { extra: { task_id: taskId } });
  }

  /** Request the shared board snapshot. */
  requestBoard(): void {
    this.send(MessageType.BoardRequest);
  }

  /** Request the live roster snapshot. */
  requestWho(): void {
    this.send(MessageType.WhoRequest);
  }

  /** Request active claims and checkpoints. */
  requestState(): void {
    this.send(MessageType.StateRequest);
  }

  /**
   * Close the connection, stop heartbeats and leave the client not ready.
   * A `connect()` still awaiting its welcome rejects; callbacks the closed
   * socket delivers afterwards are ignored.
   */
  close(): void {
    const socket = this.socket;
    const pending = this.pending;
    this.generation += 1;
    this.pending = null;
    this.socket = null;
    this.ready = false;
    this.stopHeartbeat();
    if (pending !== null) {
      clearTimeout(pending.timer);
      pending.reject(new Error(`${this.options.name} was closed before the hub welcomed it`));
    }
    socket?.close();
  }

  private sendRegistration(): void {
    const extra: Record<string, unknown> = {};
    if (this.options.token) {
      extra["token"] = this.options.token;
    }
    if (this.options.takeover) {
      extra["takeover"] = true;
    }
    this.send(MessageType.Heartbeat, { target: "System", payload: "online", extra });
  }

  private startHeartbeat(): void {
    const interval = Math.max(this.options.heartbeatIntervalMs ?? 20000, MINIMUM_HEARTBEAT_MS);
    this.heartbeatTimer = setInterval(() => {
      try {
        this.send(MessageType.Heartbeat, { target: "System", payload: "online" });
      } catch {
        this.stopHeartbeat();
      }
    }, interval);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer !== null) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private decode(data: unknown): Envelope | null {
    if (typeof data !== "string") {
      return null;
    }
    try {
      const parsed = JSON.parse(data) as unknown;
      if (typeof parsed === "object" && parsed !== null && typeof (parsed as Envelope).type === "string") {
        return parsed as Envelope;
      }
      return null;
    } catch {
      return null;
    }
  }

  private dispatch(message: Envelope): void {
    for (const handler of this.anyHandlers) {
      handler(message);
    }
    const set = this.handlers.get(message.type);
    if (set !== undefined) {
      for (const handler of set) {
        handler(message);
      }
    }
  }
}
