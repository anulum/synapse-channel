// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — Studio access HTTP cancellation and recovery

import { createServer, type ServerResponse } from "node:http";
import { expect, test } from "@playwright/test";

for (const mode of ["superseded", "deadline"]) {
  test(`access descriptor recovers after ${mode} body`, async ({ page, request }) => {
    const headers = { Authorization: `Bearer ${process.env["SYNAPSE_COCKPIT_E2E_VIEWER_TOKEN"]}` };
    const access = await request.get("/dashboard-access.json", { headers });
    expect(access.status()).toBe(200);
    const descriptor = await access.body();
    let held: ServerResponse | undefined;
    let disconnected = false;
    let count = 0;
    // Core supplies HTML, assets and descriptor; only body delivery is delayed.
    const server = createServer(async (incoming, response) => {
      if (incoming.url === "/dashboard-access.json") {
        count += 1;
        response.writeHead(200, { "Content-Type": "application/json" });
        if (count === 1) {
          held = response;
          response.on("close", () => { disconnected = true; });
          response.write('{"version":');
        } else response.end(descriptor);
        return;
      }
      try {
        const upstream = await request.get(incoming.url ?? "/", { headers });
        response.writeHead(upstream.status(), {
          "Content-Type": upstream.headers()["content-type"] ?? "application/octet-stream",
        });
        response.end(await upstream.body());
      } catch { response.writeHead(502); response.end(); }
    });
    await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    if (!address || typeof address === "string") throw new Error("No test server address");
    try {
      await page.goto(`http://127.0.0.1:${address.port}/studio/command`);
      await expect.poll(() => held !== undefined).toBe(true);
      if (mode === "superseded") {
        await page.evaluate(async () => {
          await (window as typeof window & { SynapseStudioAccess: { refresh(): Promise<unknown> } })
            .SynapseStudioAccess.refresh();
        });
      }
      await expect.poll(() => disconnected, { timeout: 7000 }).toBe(true);
      const pill = page.locator("#cc-access");
      if (mode === "deadline") await expect(pill).toHaveText("access unavailable");
      await expect(pill).toHaveText("viewer · viewer", { timeout: 7000 });
      expect(count).toBeGreaterThanOrEqual(2);
    } finally {
      try { await page.close(); }
      finally {
        held?.destroy();
        server.closeAllConnections();
        await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
      }
    }
  });
}
