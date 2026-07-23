// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — built-cockpit inspector chunk-loading acceptance

import { expect, test, type Page } from "@playwright/test";

// A controlling service worker owns module requests before Playwright routing
// can observe them. Block it in this focused gate so the deferred network
// boundary and its Suspense state remain deterministic.
test.use({ serviceWorkers: "block" });

function requiredBearer(): string {
  const value = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
  if (value === undefined || value === "") {
    throw new Error("SYNAPSE_COCKPIT_E2E_TOKEN is required by the browser gate");
  }
  return value;
}

async function scriptResources(page: Page): Promise<readonly string[]> {
  return page.evaluate(() =>
    performance
      .getEntriesByType("resource")
      .map((entry) => new URL(entry.name).pathname)
      .filter((path) => path.endsWith(".js")),
  );
}

test("non-default inspector panels load from a deferred production chunk", async ({ page }) => {
  await page.goto("/cockpit/");
  await page.getByLabel("Dashboard bearer token").fill(requiredBearer());
  await page.getByRole("button", { name: "unlock cockpit" }).click();
  await expect(page.getByRole("region", { name: "Signal log", exact: true })).toBeVisible();
  const initialScripts = new Set(await scriptResources(page));
  expect(initialScripts.size).toBe(1);

  let deferredChunk = "";
  await page.route(/\.js$/u, async (route) => {
    deferredChunk = new URL(route.request().url()).pathname;
    await new Promise((resolve) => setTimeout(resolve, 300));
    await route.continue();
  });

  await page.getByRole("tab", { name: "fleet" }).click();
  const loading = page.getByText("loading panel…", { exact: true });
  await expect(loading).toHaveAttribute("role", "status");
  await expect(page.getByLabel("Fleet communication views")).toBeVisible();
  expect(initialScripts.has(deferredChunk)).toBe(false);
  expect(deferredChunk).toMatch(/^\/cockpit\/assets\/.+\.js$/u);
  await expect
    .poll(async () => (await scriptResources(page)).filter((path) => !initialScripts.has(path)))
    .not.toEqual([]);
});
