// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — production localisation and contextual-guide acceptance

import { expect, test, type Page } from "@playwright/test";

function requiredBearer(): string {
  const value = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
  if (value === undefined || value === "") {
    throw new Error("SYNAPSE_COCKPIT_E2E_TOKEN is required by the browser gate");
  }
  return value;
}

const bearer = requiredBearer();

async function unlock(page: Page): Promise<void> {
  const input = page.getByLabel("Dashboard bearer token");
  if (await input.isVisible()) {
    await input.fill(bearer);
    await page.getByRole("button", { name: "unlock cockpit" }).click();
  }
  await expect(page.getByRole("banner").getByText("live", { exact: true })).toBeVisible();
}

test("the built cockpit guide is contextual, local, bilingual, persistent, and narrow-width safe", async ({ page }) => {
  await page.goto("/cockpit/?panel=audit&lang=en");
  await unlock(page);

  await page.keyboard.press("?");
  const guide = page.getByRole("dialog", { name: "Cockpit guide" });
  await expect(guide).toBeVisible();
  await expect(guide.getByText("Guide for Audit")).toBeVisible();
  await expect(guide).toContainText("no query or usage telemetry leaves this browser");

  await guide.getByLabel("Search the cockpit guide").fill("keyboard");
  await expect(guide.getByText("Keyboard and accessibility")).toBeVisible();
  await expect(guide.getByText("Audit", { exact: true })).toHaveCount(0);

  await guide.getByLabel("Interface language").selectOption("sk");
  await expect(page.getByRole("dialog", { name: "Príručka cockpit-u" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "sk");
  expect(new URL(page.url()).searchParams.get("lang")).toBe("sk");
  expect(await page.evaluate(() => localStorage.getItem("cockpit-locale"))).toBe("sk");

  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "sk");
  await expect(page.getByLabel("Jazyk rozhrania")).toHaveValue("sk");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Otvoriť príručku cockpit-u" }).click();
  const narrowGuide = page.getByRole("dialog", { name: "Príručka cockpit-u" });
  await expect(narrowGuide).toBeVisible();
  const box = await narrowGuide.boundingBox();
  expect(box?.x).toBe(0);
  expect(box?.width).toBe(390);
});
