// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — Fleet panel HTTP cancellation and recovery

import { createServer, type ServerResponse } from "node:http";
import { expect, test } from "@playwright/test";

const exportData = {
  version: 1, source_id: "browser-lab", exported_at: 20, advisory: true,
  snapshot: { generated_at: 19, peers: [], tasks: [], progress_notes: 0 },
};

for (const replacement of [200, 403, "deadline"] as const) {
  test(`mirror body cancellation and recovery: ${replacement}`, async ({ page, request }) => {
    const bearer = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
    expect(bearer).toBeTruthy();
    let held: ServerResponse | undefined;
    let disconnected = false;
    let mirrorRequests = 0;
    let status = replacement === "deadline" ? 200 : replacement;
    // Serve shipped Core HTML/assets, controlling only the mirror transport.
    const server = createServer(async (incoming, response) => {
      if (incoming.url === "/fleet-observed.json") {
        mirrorRequests += 1;
        if (mirrorRequests === 1) {
          held = response;
          response.on("close", () => { disconnected = true; });
          response.writeHead(200, { "Content-Type": "application/json" });
          response.write('{"version":');
          return;
        }
        response.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
        response.end(JSON.stringify(status === 200 ? exportData : { error: "locked" }));
        return;
      }
      try {
        const upstream = await request.get(incoming.url ?? "/", {
          headers: { Authorization: `Bearer ${bearer}` },
        });
        response.writeHead(upstream.status(), {
          "Content-Type": upstream.headers()["content-type"] ?? "application/octet-stream",
        });
        response.end(await upstream.body());
      } catch {
        response.writeHead(502);
        response.end();
      }
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    if (address === null || typeof address === "string") throw new Error("No TCP address");
    try {
      await page.goto(`http://127.0.0.1:${address.port}/studio/command`);
      const panel = page.getByRole("region", { name: "Fleet mirror", exact: true });
      await expect.poll(() => held !== undefined).toBe(true);
      const oldRequestFailed = page.waitForEvent("requestfailed", {
        predicate: (failed) => new URL(failed.url()).pathname === "/fleet-observed.json",
      });
      if (replacement === "deadline") {
        await expect(panel).toContainText("mirror unavailable", { timeout: 7000 });
      } else {
        await page.evaluate(async () => {
          await (window as typeof window & { SynapseStudioFleet: { refresh(): Promise<void> } })
            .SynapseStudioFleet.refresh();
        });
      }
      await oldRequestFailed;
      await expect.poll(() => disconnected).toBe(true);
      await expect(panel).toContainText(replacement === 403 ? "locked" : "exported at 20", {
        timeout: 7000,
      });
      expect(mirrorRequests).toBeGreaterThanOrEqual(2);
      status = 200;
      await page.evaluate(async () => {
        await (window as typeof window & { SynapseStudioFleet: { refresh(): Promise<void> } })
          .SynapseStudioFleet.refresh();
      });
      await expect(panel).toContainText("browser-lab");
      await expect(panel).toContainText("Complete export contains no mirrored rows");
    } finally {
      await page.close();
      held?.destroy();
      server.closeAllConnections();
      await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    }
  });
}
