// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — built-cockpit bearer and operator browser acceptance

import { expect, test } from "@playwright/test";

const bearer = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
if (bearer === undefined || bearer === "") {
  throw new Error("SYNAPSE_COCKPIT_E2E_TOKEN is required by the browser gate");
}

test("the production cockpit unlocks, writes with bearer auth, and locks on 401", async ({ page }) => {
  await page.goto("/cockpit/");
  await expect(page.getByRole("heading", { name: "Unlock operator cockpit" })).toBeVisible();
  expect(page.url()).not.toContain(bearer);

  await page.getByLabel("Dashboard bearer token").fill("wrong-e2e-bearer");
  await page.getByRole("button", { name: "unlock cockpit" }).click();
  await expect(page.getByRole("alert")).toContainText("refused");
  expect(await page.evaluate(() => sessionStorage.getItem("synapse-cockpit-bearer"))).toBeNull();

  await page.getByLabel("Dashboard bearer token").fill(bearer);
  await page.getByRole("button", { name: "unlock cockpit" }).click();
  await expect(page.getByText("live", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("synapse-cockpit-bearer"))).toBe(bearer);
  expect(await page.evaluate(() => localStorage.getItem("synapse-cockpit-bearer"))).toBeNull();

  const resourceUrls = await page.evaluate(() =>
    performance.getEntriesByType("resource").map((entry) => entry.name),
  );
  expect(resourceUrls.join("\n")).not.toContain(bearer);
  expect(await page.content()).not.toContain(bearer);

  await page.keyboard.press("Control+k");
  await page.getByRole("option", { name: "operator: send a message…" }).click();
  await page.getByLabel("Message recipient").fill("cockpit-e2e-absent-recipient");
  await page.getByLabel("Message text").fill("production browser acceptance");
  const messageRequest = page.waitForRequest(
    (request) => request.method() === "POST" && new URL(request.url()).pathname === "/message",
  );
  await page.getByRole("button", { name: "send" }).click();
  const request = await messageRequest;
  expect(request.headers()["authorization"]).toBe(`Bearer ${bearer}`);
  await expect(page.getByText(/relayed, not delivered/u)).toBeVisible();

  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
  });
  const cachedRequests = await page.evaluate(async () => {
    const records: { url: string; authorization: string | null }[] = [];
    for (const cacheName of await caches.keys()) {
      const cache = await caches.open(cacheName);
      for (const cached of await cache.keys()) {
        records.push({ url: cached.url, authorization: cached.headers.get("Authorization") });
      }
    }
    return records;
  });
  expect(cachedRequests.length).toBeGreaterThan(0);
  expect(cachedRequests.every((cached) => cached.authorization === null)).toBe(true);
  expect(JSON.stringify(cachedRequests)).not.toContain(bearer);

  await page.route("**/snapshot.json*", async (route) => {
    await route.fulfill({ status: 401, contentType: "text/plain", body: "revoked\n" });
  });
  await expect(page.getByRole("heading", { name: "Unlock operator cockpit" })).toBeVisible({
    timeout: 8_000,
  });
  expect(await page.evaluate(() => sessionStorage.getItem("synapse-cockpit-bearer"))).toBeNull();
  await expect(page.getByText("agents online", { exact: true })).toHaveCount(0);
});
