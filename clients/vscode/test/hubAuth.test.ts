// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — tests for VS Code hub authentication policy

import { describe, expect, it } from "vitest";
import {
  HubCredentialStore,
  hubConnectionVerdict,
  hubTokenKey,
  registrationHeartbeat,
  type SecretStorageLike,
} from "../src/hubAuth.js";

class MemorySecrets implements SecretStorageLike {
  readonly values = new Map<string, string>();

  get(key: string): PromiseLike<string | undefined> {
    return Promise.resolve(this.values.get(key));
  }

  store(key: string, value: string): PromiseLike<void> {
    this.values.set(key, value);
    return Promise.resolve();
  }

  delete(key: string): PromiseLike<void> {
    this.values.delete(key);
    return Promise.resolve();
  }
}

describe("hubConnectionVerdict", () => {
  it.each([
    "ws://localhost:8876",
    "ws://127.0.0.1:8876",
    "ws://127.17.4.9:8876",
    "ws://[::1]:8876",
    "wss://hub.example:8876/synapse",
  ])("allows a protected transport boundary: %s", (uri) => {
    expect(hubConnectionVerdict(uri)).toEqual({ allowed: true, uri: new URL(uri).toString() });
  });

  it.each(["ws://192.168.1.20:8876", "ws://hub.example:8876"])(
    "refuses a remote plaintext WebSocket: %s",
    (uri) => {
      expect(hubConnectionVerdict(uri)).toEqual({
        allowed: false,
        reason: "Remote SYNAPSE hubs require wss://; plaintext ws:// is allowed only on loopback.",
      });
    },
  );

  it.each([
    "https://hub.example",
    "not a URL",
    "wss://token@hub.example:8876",
    "wss://hub.example:8876/?token=secret",
    "wss://hub.example:8876/#secret",
  ])("refuses an invalid or secret-bearing URI: %s", (uri) => {
    expect(hubConnectionVerdict(uri).allowed).toBe(false);
  });
});

describe("registrationHeartbeat", () => {
  it("uses the protocol heartbeat and omits an absent token", () => {
    expect(registrationHeartbeat("workspace/vscode")).toEqual({
      type: "heartbeat",
      sender: "workspace/vscode",
      target: "System",
      payload: "online",
    });
  });

  it("places the shared token only on the first registration frame", () => {
    expect(registrationHeartbeat("workspace/vscode", "secret value")).toEqual({
      type: "heartbeat",
      sender: "workspace/vscode",
      target: "System",
      payload: "online",
      token: "secret value",
    });
  });
});

describe("HubCredentialStore", () => {
  it("round-trips independent per-hub tokens through the secret store", async () => {
    const secrets = new MemorySecrets();
    const credentials = new HubCredentialStore(secrets);
    await credentials.store("wss://east.example:8876", "east token");
    await credentials.store("wss://west.example:8876", "west token");

    await expect(credentials.get("wss://east.example:8876/")).resolves.toBe("east token");
    await expect(credentials.get("wss://west.example:8876")).resolves.toBe("west token");
    expect([...secrets.values.keys()]).toEqual([
      hubTokenKey("wss://east.example:8876"),
      hubTokenKey("wss://west.example:8876"),
    ]);
  });

  it("clears only the selected hub and refuses an empty token", async () => {
    const credentials = new HubCredentialStore(new MemorySecrets());
    await credentials.store("ws://127.0.0.1:8876", "local token");
    await credentials.clear("ws://127.0.0.1:8876/");

    await expect(credentials.get("ws://127.0.0.1:8876")).resolves.toBeUndefined();
    await expect(credentials.store("ws://127.0.0.1:8876", "   ")).rejects.toThrow(
      "token cannot be empty",
    );
  });
});
