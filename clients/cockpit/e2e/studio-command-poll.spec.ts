// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — main Studio snapshot authentication and timeout recovery

import { createServer, type ServerResponse } from "node:http";
import { expect, test } from "@playwright/test";

test("session-authenticated snapshots mark old data offline and recover after a stalled body", async ({ page, request }) => {
  const bearer = process.env["SYNAPSE_COCKPIT_E2E_VIEWER_TOKEN"];
  expect(bearer).toBeTruthy();
  let snapshots = 0;
  let held: ServerResponse | undefined;
  let disconnected = false;
  const auth: string[] = [];
  // Only HTML/assets use fixture authentication; snapshots must supply their own.
  const server = createServer(async (incoming, response) => {
    const snapshot = incoming.url === "/studio.json";
    const authorization = snapshot ? incoming.headers.authorization ?? "" : `Bearer ${bearer}`;
    try {
      const upstream = await request.get(incoming.url ?? "/", { headers: { Authorization: authorization } });
      response.writeHead(upstream.status(), {
        "Content-Type": upstream.headers()["content-type"] ?? "application/octet-stream",
      });
      if (snapshot) {
        snapshots += 1;
        auth.push(authorization);
        if (snapshots === 2 && upstream.status() === 200) {
          held = response;
          response.on("close", () => { disconnected = true; });
          response.write('{"hub":');
          return;
        }
      }
      response.end(await upstream.body());
    } catch { response.writeHead(502); response.end(); }
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("No test server address");
  try {
    await page.addInitScript(token => sessionStorage.setItem("synapse-cockpit-bearer", token!), bearer);
    await page.goto(`http://127.0.0.1:${address.port}/studio/command`);
    await expect(page.locator("#cc-connection")).toHaveText("connected");
    const hub = await page.locator("#cc-hub").textContent();
    await expect.poll(() => held !== undefined).toBe(true);
    await expect.poll(() => disconnected, { timeout: 7000 }).toBe(true);
    await expect(page.locator("#cc-connection")).toHaveText("offline");
    await expect(page.locator("#cc-offline")).toContainText("not current");
    await expect(page.locator("#cc-hub")).toHaveText(hub!);
    await expect(page.locator("#cc-connection")).toHaveText("connected", { timeout: 7000 });
    await expect(page.locator("#cc-offline")).toBeHidden();
    expect(snapshots).toBeGreaterThanOrEqual(3);
    expect(auth.every(value => value === `Bearer ${bearer}`)).toBe(true);
  } finally {
    try { await page.close(); }
    finally {
      held?.destroy();
      server.closeAllConnections();
      await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    }
  }
});
