// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — tests for latest-only asynchronous credential reads

import { describe, expect, it } from "vitest";
import { ConfigurationReconnectGate } from "../src/configurationReconnect.js";

function deferred<T>(): {
  promise: Promise<T>;
  resolve(value: T): void;
  reject(error: Error): void;
} {
  let resolvePromise!: (value: T) => void;
  let rejectPromise!: (error: Error) => void;
  const promise = new Promise<T>((resolve, reject) => {
    resolvePromise = resolve;
    rejectPromise = reject;
  });
  return { promise, resolve: resolvePromise, reject: rejectPromise };
}

describe("ConfigurationReconnectGate", () => {
  it("discards an older SecretStorage read that finishes after the current read", async () => {
    const gate = new ConfigurationReconnectGate();
    const oldToken = deferred<string>();
    const newToken = deferred<string>();
    const oldRead = gate.read(gate.begin(), () => oldToken.promise);
    const newRead = gate.read(gate.begin(), () => newToken.promise);

    newToken.resolve("new-token");
    await expect(newRead).resolves.toEqual({ kind: "current", value: "new-token" });
    oldToken.resolve("old-token");
    await expect(oldRead).resolves.toEqual({ kind: "stale" });
  });

  it("reports only a current read failure", async () => {
    const gate = new ConfigurationReconnectGate();
    const stale = deferred<string>();
    const staleRead = gate.read(gate.begin(), () => stale.promise);
    const currentRead = gate.read(gate.begin(), async () => {
      throw new Error("SecretStorage unavailable");
    });
    await expect(currentRead).resolves.toEqual({ kind: "error" });
    stale.reject(new Error("old failure"));
    await expect(staleRead).resolves.toEqual({ kind: "stale" });
  });
});
