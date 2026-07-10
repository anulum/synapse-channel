// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — secret-safe VS Code hub authentication policy

/** A structural subset of VS Code SecretStorage, kept editor-agnostic for tests. */
export interface SecretStorageLike {
  get(key: string): PromiseLike<string | undefined>;
  store(key: string, value: string): PromiseLike<void>;
  delete(key: string): PromiseLike<void>;
}

/** A usable WebSocket URI or the fail-closed reason it was rejected. */
export type HubConnectionVerdict =
  | { allowed: true; uri: string }
  | { allowed: false; reason: string };

const TOKEN_KEY_PREFIX = "synapse.hubToken:";

function parsedHubUri(value: string): HubConnectionVerdict {
  let uri: URL;
  try {
    uri = new URL(value.trim());
  } catch {
    return { allowed: false, reason: "SYNAPSE hub URI is not a valid URL." };
  }
  if (uri.protocol !== "ws:" && uri.protocol !== "wss:") {
    return { allowed: false, reason: "SYNAPSE hub URI must use ws:// or wss://." };
  }
  if (uri.username || uri.password || uri.search || uri.hash) {
    return {
      allowed: false,
      reason: "SYNAPSE hub URI must not contain credentials, query parameters, or a fragment.",
    };
  }
  return { allowed: true, uri: uri.toString() };
}

function isLoopbackHost(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (host === "localhost" || host === "::1") {
    return true;
  }
  const octets = host.split(".");
  if (octets.length !== 4) {
    return false;
  }
  const numbers = octets.map((octet) => Number(octet));
  return numbers.every((octet) => Number.isInteger(octet) && octet >= 0 && octet <= 255)
    && numbers[0] === 127;
}

/**
 * Validate a configured hub transport before opening a socket.
 *
 * Plain WebSockets are accepted only on loopback. A remote token, identity, or
 * coordination frame must never cross a plaintext transport; shared hubs use
 * `wss://` with a certificate trusted by the editor host.
 */
export function hubConnectionVerdict(value: string): HubConnectionVerdict {
  const parsed = parsedHubUri(value);
  if (!parsed.allowed) {
    return parsed;
  }
  const uri = new URL(parsed.uri);
  if (uri.protocol === "ws:" && !isLoopbackHost(uri.hostname)) {
    return {
      allowed: false,
      reason: "Remote SYNAPSE hubs require wss://; plaintext ws:// is allowed only on loopback.",
    };
  }
  return parsed;
}

/** Build the first, identity-binding heartbeat for an open or token-gated hub. */
export function registrationHeartbeat(identity: string, token?: string): Record<string, unknown> {
  const frame: Record<string, unknown> = {
    type: "heartbeat",
    sender: identity,
    target: "System",
    payload: "online",
  };
  if (token !== undefined && token.length > 0) {
    frame["token"] = token;
  }
  return frame;
}

/** Return the per-hub SecretStorage key without putting a credential in settings. */
export function hubTokenKey(value: string): string {
  const parsed = parsedHubUri(value);
  if (!parsed.allowed) {
    throw new Error(parsed.reason);
  }
  return `${TOKEN_KEY_PREFIX}${parsed.uri}`;
}

/** Store one independent encrypted token per canonical hub URI. */
export class HubCredentialStore {
  constructor(private readonly secrets: SecretStorageLike) {}

  async get(uri: string): Promise<string | undefined> {
    const token = await this.secrets.get(hubTokenKey(uri));
    return token !== undefined && token.trim().length > 0 ? token : undefined;
  }

  async store(uri: string, token: string): Promise<void> {
    if (token.trim().length === 0) {
      throw new Error("SYNAPSE hub token cannot be empty.");
    }
    await this.secrets.store(hubTokenKey(uri), token);
  }

  async clear(uri: string): Promise<void> {
    await this.secrets.delete(hubTokenKey(uri));
  }
}
