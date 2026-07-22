// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — versioned multiplexed cockpit live transport

import { authenticatedFetch } from "./auth";

export const LIVE_TRANSPORT_VERSION = 1;
export const LIVE_TRANSPORT_URL = "/live.ndjson";
export const MAX_LIVE_FRAME_BYTES = 8_388_608;

export type LiveChannel = "snapshot" | "events" | "receipts" | "operator_actions";
export type LiveChannelStatus = "live" | "unchanged" | "absent" | "error";
export type LiveConnectionStatus =
  | "connecting"
  | "live"
  | "reconnecting"
  | "gap"
  | "fallback"
  | "unsupported"
  | "stopped";

export interface LiveFrame {
  readonly version: 1;
  readonly connectionId: string;
  readonly sequence: number;
  readonly kind: "hello" | "channel" | "close";
  readonly sentAt: number;
  readonly channel?: LiveChannel;
  readonly status?: string;
  readonly data?: unknown;
  readonly detail?: string;
}

export interface LiveChannelFrame extends LiveFrame {
  readonly kind: "channel";
  readonly channel: LiveChannel;
  readonly status: LiveChannelStatus;
}

export interface LiveConnectionState {
  readonly status: LiveConnectionStatus;
  readonly attempt: number;
  readonly detail: string | null;
}

export interface LiveTransport {
  subscribeFrames(listener: (frame: LiveChannelFrame) => void): () => void;
  subscribeState(listener: (state: LiveConnectionState) => void): () => void;
  stop(): void;
}

export interface LiveTransportOptions {
  readonly url?: string;
  readonly fetcher?: typeof fetch;
  readonly minimumBackoffMs?: number;
  readonly maximumBackoffMs?: number;
  readonly wait?: (milliseconds: number, signal: AbortSignal) => Promise<void>;
}

function recordOf(raw: unknown): Record<string, unknown> | null {
  return typeof raw === "object" && raw !== null && !Array.isArray(raw)
    ? (raw as Record<string, unknown>)
    : null;
}

function liveChannel(raw: unknown): LiveChannel | null {
  return raw === "snapshot" ||
    raw === "events" ||
    raw === "receipts" ||
    raw === "operator_actions"
    ? raw
    : null;
}

function liveChannelStatus(raw: unknown): LiveChannelStatus | null {
  return raw === "live" || raw === "unchanged" || raw === "absent" || raw === "error"
    ? raw
    : null;
}

/** Parse one untrusted NDJSON line into the exact version-1 envelope. */
export function parseLiveFrame(line: string): LiveFrame | null {
  let decoded: unknown;
  try {
    decoded = JSON.parse(line);
  } catch {
    return null;
  }
  const frame = recordOf(decoded);
  if (frame === null || frame["version"] !== LIVE_TRANSPORT_VERSION) return null;
  const connectionId = frame["connection_id"];
  const sequence = frame["sequence"];
  const kind = frame["kind"];
  const sentAt = frame["sent_at"];
  if (
    typeof connectionId !== "string" ||
    connectionId.length === 0 ||
    typeof sequence !== "number" ||
    !Number.isSafeInteger(sequence) ||
    sequence < 1 ||
    (kind !== "hello" && kind !== "channel" && kind !== "close") ||
    typeof sentAt !== "number" ||
    !Number.isFinite(sentAt) ||
    sentAt < 0
  ) {
    return null;
  }
  const detail = typeof frame["detail"] === "string" ? frame["detail"] : undefined;
  const status = typeof frame["status"] === "string" ? frame["status"] : undefined;
  if (kind === "channel") {
    const channel = liveChannel(frame["channel"]);
    const channelStatus = liveChannelStatus(status);
    if (channel === null || channelStatus === null) return null;
    if (channelStatus === "unchanged" && channel !== "snapshot") return null;
    return {
      version: LIVE_TRANSPORT_VERSION,
      connectionId,
      sequence,
      kind,
      sentAt,
      channel,
      status: channelStatus,
      ...(frame["data"] !== undefined ? { data: frame["data"] } : {}),
      ...(detail !== undefined ? { detail } : {}),
    };
  }
  return {
    version: LIVE_TRANSPORT_VERSION,
    connectionId,
    sequence,
    kind,
    sentAt,
    ...(status !== undefined ? { status } : {}),
    ...(frame["data"] !== undefined ? { data: frame["data"] } : {}),
    ...(detail !== undefined ? { detail } : {}),
  };
}

function defaultWait(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, milliseconds);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

function errorDetail(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

/** Open and maintain one authenticated stream with bounded reconnect backoff. */
export function createLiveTransport(options: LiveTransportOptions = {}): LiveTransport {
  const fetcher = options.fetcher ?? authenticatedFetch;
  const url = options.url ?? LIVE_TRANSPORT_URL;
  const minimumBackoffMs = Math.max(10, options.minimumBackoffMs ?? 500);
  const maximumBackoffMs = Math.max(minimumBackoffMs, options.maximumBackoffMs ?? 10_000);
  const wait = options.wait ?? defaultWait;
  const frameListeners = new Set<(frame: LiveChannelFrame) => void>();
  const stateListeners = new Set<(state: LiveConnectionState) => void>();
  const lifecycle = new AbortController();
  let request: AbortController | undefined;
  let stopped = false;
  let state: LiveConnectionState = { status: "connecting", attempt: 0, detail: null };

  const publishState = (next: LiveConnectionState): void => {
    state = next;
    for (const listener of stateListeners) listener(state);
  };

  const run = async (): Promise<void> => {
    let attempt = 0;
    while (!stopped) {
      request = new AbortController();
      try {
        const response = await fetcher(url, {
          headers: { Accept: "application/x-ndjson" },
          signal: request.signal,
        });
        if (response.status === 404) {
          publishState({ status: "unsupported", attempt, detail: "live transport unavailable" });
          return;
        }
        if (!response.ok) throw new Error(`live transport returned ${response.status}`);
        if (response.body === null) throw new Error("live transport response had no body");

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let connectionId: string | null = null;
        let expectedSequence = 1;
        let receivedFrame = false;
        publishState({ status: "live", attempt, detail: null });

        while (!stopped) {
          const result = await reader.read();
          buffer += decoder.decode(result.value, { stream: !result.done });
          if (buffer.length > MAX_LIVE_FRAME_BYTES) {
            throw new Error("live transport frame exceeded the byte limit");
          }
          const lines = buffer.split("\n");
          // String.split() always returns at least one entry.
          buffer = lines.pop() as string;
          for (const line of lines) {
            if (line === "") continue;
            const frame = parseLiveFrame(line);
            if (frame === null) throw new Error("live transport emitted an invalid frame");
            if (connectionId === null) connectionId = frame.connectionId;
            if (frame.connectionId !== connectionId || frame.sequence !== expectedSequence) {
              publishState({
                status: "gap",
                attempt,
                detail: `expected sequence ${expectedSequence}`,
              });
              throw new Error("live transport sequence gap");
            }
            expectedSequence += 1;
            receivedFrame = true;
            if (frame.kind === "channel") {
              for (const listener of frameListeners) listener(frame as LiveChannelFrame);
            }
          }
          if (result.done) break;
        }
        if (stopped) return;
        if (receivedFrame) attempt = 0;
        throw new Error("live transport closed");
      } catch (cause) {
        if (stopped) return;
        attempt += 1;
        const delay = Math.min(maximumBackoffMs, minimumBackoffMs * 2 ** (attempt - 1));
        if (state.status !== "gap") {
          publishState({ status: "reconnecting", attempt, detail: errorDetail(cause) });
        }
        await wait(delay, lifecycle.signal);
      }
    }
  };

  void run();
  return {
    subscribeFrames(listener) {
      frameListeners.add(listener);
      return () => frameListeners.delete(listener);
    },
    subscribeState(listener) {
      stateListeners.add(listener);
      listener(state);
      return () => stateListeners.delete(listener);
    },
    stop() {
      if (stopped) return;
      stopped = true;
      lifecycle.abort();
      request?.abort();
      publishState({ status: "stopped", attempt: state.attempt, detail: null });
      frameListeners.clear();
      stateListeners.clear();
    },
  };
}
