// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — typed WebSocket client for the coordination hub

import { SocketClosureBarrier } from "./socket-closure.js";

import {
  type ClaimScopeIdentity,
  type Envelope,
  MessageType,
  MIN_ATTACHMENT_PROTOCOL_VERSION,
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
  /** Sign the registration envelope with an enrolled identity key. */
  signRegistration?: (frame: Envelope) => Envelope;
  /** Sign each attachment frame with the Hub's configured per-message key. */
  signAttachment?: (frame: Envelope) => Envelope;
}

const ATTACHMENT_REQUEST_TYPES = new Set<string>([
  MessageType.AttachmentBegin, MessageType.AttachmentChunk, MessageType.AttachmentCommit,
  MessageType.AttachmentAbort, MessageType.AttachmentInfo, MessageType.AttachmentRead,
  MessageType.AttachmentRef, MessageType.AttachmentGc,
]);

const MINIMUM_HEARTBEAT_MS = 1000;
const HUB_WHITESPACE = "[\\u0009-\\u000d\\u001c-\\u0020\\u0085\\u00a0\\u1680" +
  "\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]";
const HUB_TASK_EDGES = new RegExp(`^${HUB_WHITESPACE}+|${HUB_WHITESPACE}+$`, "g");

/** Match the Python hub's task-id stripping, including NEL and preserving BOM. */
function normalizedTaskId(taskId: string): string {
  return taskId.replace(HUB_TASK_EDGES, "");
}

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
  private hubProtocolVersion: number | null = null;
  /** Fencing epoch of each lease this client holds, from its own grants. */
  private readonly leaseEpochs = new Map<string, number>();
  /** Incremented on every connect and close; callbacks of an older socket are ignored. */
  private generation = 0;
  private pending: PendingAttempt | null = null;
  private readonly closing = new SocketClosureBarrier();
  private awaitingClosure = false;

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
   * `close()` first. A reconnect waits for the prior close event, with a
   * five-second deadline, rather than competing with the old name binding.
   */
  async connect(): Promise<void> {
    if (this.socket !== null || this.awaitingClosure) {
      return Promise.reject(
        new Error(`${this.options.name} already has an open or pending connection; close() it first`),
      );
    }
    const closure = this.closing.pending;
    if (closure !== null) {
      this.awaitingClosure = true;
      const generation = this.generation;
      try {
        await closure;
      } finally {
        this.awaitingClosure = false;
      }
      if (generation !== this.generation) {
        throw new Error(`${this.options.name} was closed while awaiting its prior socket`);
      }
    }
    const factory = this.options.webSocketFactory ?? defaultFactory;
    const socket = factory(this.options.uri);
    const generation = ++this.generation;
    this.socket = socket;
    this.ready = false;
    this.hubProtocolVersion = null;
    return new Promise<void>((resolve, reject) => {
      const live = (): boolean => generation === this.generation && this.socket === socket;
      const fail = (error: Error): void => {
        this.generation += 1;
        this.pending = null;
        clearTimeout(timer);
        this.stopHeartbeat();
        this.ready = false;
        this.socket = null;
        try {
          this.closing.close(socket);
        } catch (closeError) {
          reject(closeError);
          return;
        }
        reject(error);
      };
      const timer = setTimeout(() => {
        if (!live()) {
          return;
        }
        fail(new Error(`hub did not welcome ${this.options.name} in time`));
      }, this.options.readyTimeoutMs ?? 5000);
      this.pending = { timer, reject };

      socket.onopen = () => {
        if (!live()) {
          return;
        }
        try {
          this.sendRegistration();
          this.startHeartbeat();
        } catch (error) {
          fail(error instanceof Error ? error : new Error("registration signer failed"));
        }
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
          this.hubProtocolVersion = typeof message["protocol_version"] === "number"
            ? message["protocol_version"] as number : null;
          this.ready = true;
          this.pending = null;
          clearTimeout(timer);
          resolve();
        }
        this.trackLeaseEpoch(message);
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

  /** Send a version-four attachment request through the configured signed boundary. */
  attachment(type: string, extra: Record<string, unknown>): void {
    if (!ATTACHMENT_REQUEST_TYPES.has(type)) {
      throw new Error("unknown attachment request type");
    }
    if (!this.ready || this.socket === null || this.hubProtocolVersion === null ||
        this.hubProtocolVersion < MIN_ATTACHMENT_PROTOCOL_VERSION) {
      throw new Error("hub does not advertise attachment protocol version four");
    }
    const signer = this.options.signAttachment;
    if (signer === undefined) {
      throw new Error("attachment frames require a configured per-message signer");
    }
    const frame = buildEnvelope(this.options.name, type, { target: "SynapseHub", extra });
    this.socket.send(JSON.stringify(signer(frame)));
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
   * Claim a task on display paths, a whole worktree, or the task alone.
   *
   * The identity is an additive wire field. Callers that cannot derive it may
   * omit it and retain legacy literal-path comparison. Git-aware callers should
   * use the Python resolver rather than inventing canonical values. With no
   * paths, pass the worktree's `pathIdentity` to claim that whole worktree, or
   * `{ taskOnly: true }` for a lock on the task id with no file scope; a claim
   * with neither is refused, because the hub would treat it as a lock over its
   * shared default namespace.
   */
  claim(
    taskId: string,
    paths: string[] = [],
    pathIdentity?: ClaimScopeIdentity,
    options: { taskOnly?: boolean } = {},
  ): void {
    if (options.taskOnly === true) {
      if (paths.length > 0 || pathIdentity !== undefined) {
        throw new Error("taskOnly cannot be combined with paths or a path identity");
      }
      this.send(MessageType.Claim, { extra: { task_id: taskId, paths: [], worktree: taskId } });
      return;
    }
    if (paths.length === 0 && pathIdentity === undefined) {
      throw new Error(
        "a claim without paths needs the worktree pathIdentity or { taskOnly: true }",
      );
    }
    const extra: Record<string, unknown> = { task_id: taskId, paths };
    if (pathIdentity !== undefined) {
      extra["worktree"] = pathIdentity.worktree_path;
      extra["path_identity"] = pathIdentity;
    }
    this.send(MessageType.Claim, { extra });
  }

  /**
   * Release a claim you own.
   *
   * The frame names the lease's fencing epoch: `epoch` when given, otherwise
   * the epoch from this client's own `claim_granted` or `handoff_granted` for
   * the task. A hub started with `--require-fencing-epoch` (forced by
   * `--team-secure` and `--secure`) refuses a release without it.
   */
  release(taskId: string, epoch?: number, idemKey?: string): void {
    const request = this.prepareRelease(taskId, epoch, idemKey);
    const { sender: _sender, type: _type, target, payload, timestamp: _timestamp, ...extra } = request;
    this.send(MessageType.Release, { target, payload, extra });
  }

  /** Prepare a release without sending; retain its key, epoch and semantic SHA-256 for recovery. */
  prepareRelease(
    taskId: string, epoch?: number, idemKey?: string,
  ): Envelope & { task_id: string; epoch?: number; idem_key?: string } {
    const task = normalizedTaskId(taskId);
    const extra: { task_id: string; epoch?: number; idem_key?: string } = { task_id: task };
    const fence = epoch ?? this.leaseEpochs.get(task);
    if (fence !== undefined) {
      extra["epoch"] = fence;
    }
    if (idemKey !== undefined) extra["idem_key"] = idemKey;
    return { ...buildEnvelope(this.options.name, MessageType.Release, { extra }), ...extra };
  }

  /** The fencing epoch this client holds for `taskId`, if it was granted one. */
  leaseEpoch(taskId: string): number | undefined {
    return this.leaseEpochs.get(taskId);
  }

  /** Request the shared board snapshot. */
  requestBoard(): void {
    this.send(MessageType.BoardRequest);
  }

  /**
   * Request the live roster snapshot. With `hub`, the connected hub asks that
   * message peer for its roster (wire version 5); seats come back as
   * `seat@hub`, or an `error` frame arrives when the peer cannot be asked.
   */
  requestWho(hub?: string): void {
    this.send(MessageType.WhoRequest, hub ? { extra: { hub } } : {});
  }

  /** Request active claims and checkpoints. */
  requestState(): void {
    this.send(MessageType.StateRequest);
  }

  /** Read an exact durable release; an unknown or legacy snapshot never confirms success. */
  requestReleaseConfirmation(
    taskId: string, operationId: string, requestDigest: string, requestId: string,
  ): void {
    this.send(MessageType.StateRequest, {
      target: "System", payload: "release confirmation",
      extra: { request_id: requestId, release_confirmation: {
        task_id: normalizedTaskId(taskId), operation_id: operationId, request_digest: requestDigest,
      } },
    });
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
    if (socket !== null) this.closing.close(socket);
  }

  private sendRegistration(): void {
    const extra: Record<string, unknown> = { protocol_version: MIN_ATTACHMENT_PROTOCOL_VERSION };
    if (this.options.token) {
      extra["token"] = this.options.token;
    }
    if (this.options.takeover) {
      extra["takeover"] = true;
    }
    if (this.options.signRegistration !== undefined) {
      if (this.socket === null) {
        throw new Error("client is not connected");
      }
      const frame = buildEnvelope(this.options.name, MessageType.Heartbeat,
        { target: "System", payload: "online", extra });
      this.socket.send(JSON.stringify(this.options.signRegistration(frame)));
      return;
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

  /** Remember the epoch of a lease granted to this client; forget it when it ends. */
  private trackLeaseEpoch(message: Envelope): void {
    const taskId = message["task_id"];
    if (typeof taskId !== "string") {
      return;
    }
    const epoch = message["epoch"];
    const granted = message.type === MessageType.ClaimGranted ||
      message.type === MessageType.HandoffGranted;
    if (granted && message["owner"] === this.options.name &&
        typeof epoch === "number" && Number.isSafeInteger(epoch) && epoch >= 0) {
      this.leaseEpochs.set(taskId, epoch);
    } else if (message.type === MessageType.ReleaseGranted ||
        message.type === MessageType.HandoffGranted) {
      this.leaseEpochs.delete(taskId);
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
