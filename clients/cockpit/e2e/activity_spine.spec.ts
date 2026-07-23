// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — built-cockpit activity-spine interaction acceptance

import { expect, test } from "@playwright/test";

function requiredBearer(): string {
  const value = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
  if (value === undefined || value === "") {
    throw new Error("SYNAPSE_COCKPIT_E2E_TOKEN is required by the browser gate");
  }
  return value;
}

test("the production spine brushes by keyboard and pointer without hiding its textual peer", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.goto("/cockpit/");
  await page.getByLabel("Dashboard bearer token").fill(requiredBearer());
  await page.getByRole("button", { name: "unlock cockpit" }).click();
  await expect(page.getByRole("banner").getByText("live", { exact: true })).toBeVisible();

  const spine = page.getByLabel(/Activity spine\. Drag or use the arrow keys/u);
  const signalLog = page.getByRole("region", { name: "Signal log", exact: true });
  await expect(spine).toBeVisible();
  await expect(signalLog).toBeVisible();

  await spine.focus();
  await spine.press("ArrowLeft");
  await expect(page.getByRole("button", { name: "Clear brushed time window" })).toBeVisible();
  await spine.press("]");
  await expect(signalLog).toBeVisible();
  await spine.press("Escape");
  await expect(page.getByRole("button", { name: "Clear brushed time window" })).toHaveCount(0);

  const bounds = await spine.boundingBox();
  expect(bounds).not.toBeNull();
  if (bounds === null) return;
  await page.mouse.move(bounds.x + bounds.width * 0.25, bounds.y + bounds.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width * 0.7, bounds.y + bounds.height * 0.5);
  await page.mouse.up();
  await expect(page.getByRole("button", { name: "Clear brushed time window" })).toBeVisible();
  expect(pageErrors).toEqual([]);
});
