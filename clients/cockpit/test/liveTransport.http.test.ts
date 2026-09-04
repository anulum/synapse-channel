// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — live HTTP stream teardown regression

import { createServer, type ServerResponse } from "node:http";
import { setTimeout as delay } from "node:timers/promises";
import { expect, it } from "vitest";

import { createLiveTransport, type LiveTransport } from "../src/lib/liveTransport";

function frame(sequence: number, kind = "hello"): string {
  return JSON.stringify({
    version: 1, connection_id: "http-stream", sequence, kind, sent_at: 1,
    ...(kind === "channel" ? { channel: "events", status: "live", data: {} } : {}),
  }) + "\n";
}

async function eventually(predicate: () => boolean): Promise<void> {
  const deadline = Date.now() + 2000;
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error("HTTP stream cleanup did not complete");
    await delay(5);
  }
}

it.each(["invalid", "gap", "listener", "503", "404", "stop", "backoff"] as const)(
  "closes real HTTP responses for %s before another attempt",
  async (scenario) => {
    const responses = new Set<ServerResponse>();
    let requests = 0;
    let waits = 0;
    let cleanupFailure: unknown;
    const server = createServer((_request, response) => {
      requests += 1;
      responses.add(response);
      response.on("close", () => responses.delete(response));
      response.writeHead(scenario === "503" ? 503 : scenario === "404" ? 404 : 200, {
        "Content-Type": "application/x-ndjson",
      });
      const payload = scenario === "gap" ? frame(1) + frame(3, "channel")
        : scenario === "listener" ? frame(1) + frame(2, "channel")
          : scenario === "stop" ? frame(1) : "invalid\n";
      response.write(payload);
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    if (address === null || typeof address === "string") throw new Error("missing TCP address");
    let transport: LiveTransport | undefined;
    try {
      transport = createLiveTransport({
        url: `http://127.0.0.1:${address.port}/live.ndjson`,
        fetcher: fetch,
        wait: async () => {
          waits += 1;
          try {
            await eventually(() => responses.size === 0);
          } catch (cause) {
            cleanupFailure = cause;
          }
          if (waits >= 3 || scenario === "backoff" || cleanupFailure !== undefined) {
            transport?.stop();
          }
        },
      });
      if (scenario === "listener") {
        transport.subscribeFrames(() => { throw new Error("consumer failed"); });
      }
      if (scenario === "stop") {
        await eventually(() => requests === 1);
        transport.stop();
      }
      if (scenario === "404" || scenario === "stop") {
        await eventually(() => requests === 1 && responses.size === 0);
        expect(requests).toBe(1);
      } else {
        await eventually(() => waits >= (scenario === "backoff" ? 1 : 3) || cleanupFailure !== undefined);
        await eventually(() => responses.size === 0 || cleanupFailure !== undefined);
        expect(cleanupFailure).toBeUndefined();
        expect(requests).toBe(scenario === "backoff" ? 1 : 3);
      }
      transport.stop();
      await eventually(() => responses.size === 0);
    } finally {
      transport?.stop();
      server.closeAllConnections();
      await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
    }
  },
);
