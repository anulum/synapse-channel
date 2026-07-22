// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — production localisation and contextual-guide acceptance

import { expect, test, type Page } from "@playwright/test";
import axe from "axe-core";

interface AxeWindow extends Window {
  readonly axe: {
    run(root: Document | Element): Promise<{ readonly violations: readonly { readonly id: string }[] }>;
  };
}

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

test("the built cockpit guide is contextual, local, trilingual, persistent, and narrow-width safe", async ({ page }) => {
  await page.addInitScript({ content: axe.source });
  await page.goto("/cockpit/?panel=audit&lang=en");
  await unlock(page);
  const switchToDark = page.getByRole("button", { name: "Switch to dark theme" });
  if (await switchToDark.isVisible()) await switchToDark.click();
  await expect(page.getByRole("button", { name: "Switch to light theme" })).toBeVisible();

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

  await page.getByRole("dialog", { name: "Príručka cockpit-u" })
    .getByLabel("Jazyk rozhrania").selectOption("de");
  const germanGuide = page.getByRole("dialog", { name: "Cockpit-Handbuch" });
  await expect(germanGuide).toBeVisible();
  await expect(germanGuide).toContainText("Nutzungsmetrik verlässt diesen Browser");
  await expect(page.locator("html")).toHaveAttribute("lang", "de");
  expect(new URL(page.url()).searchParams.get("lang")).toBe("de");
  expect(await page.evaluate(() => localStorage.getItem("cockpit-locale"))).toBe("de");
  const darkResult = await page.evaluate(async () =>
    (window as unknown as AxeWindow).axe.run(document),
  );
  expect(darkResult.violations, JSON.stringify(darkResult.violations)).toEqual([]);

  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "Zum hellen Design wechseln" }).click();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "de");
  await expect(page.getByLabel("Sprache der Benutzeroberfläche")).toHaveValue("de");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Cockpit-Handbuch öffnen" }).click();
  const narrowGuide = page.getByRole("dialog", { name: "Cockpit-Handbuch" });
  await expect(narrowGuide).toBeVisible();
  const box = await narrowGuide.boundingBox();
  expect(box?.x).toBe(0);
  expect(box?.width).toBe(390);
  expect(await narrowGuide.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  const lightResult = await page.evaluate(async () =>
    (window as unknown as AxeWindow).axe.run(document),
  );
  expect(lightResult.violations, JSON.stringify(lightResult.violations)).toEqual([]);

  await narrowGuide.getByRole("button", { name: "Read-only Einrichtungsassistenten öffnen" }).click();
  const germanSetup = page.getByRole("dialog", { name: "Einrichtungsassistent" });
  await expect(germanSetup).toBeVisible();
  expect(await germanSetup.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);

  await germanSetup.getByRole("button", { name: "Einrichtungsassistenten schließen" }).click();
  await page.setViewportSize({ width: 844, height: 390 });
  await page.getByRole("button", { name: "Cockpit-Handbuch öffnen" }).click();
  const landscapeGuide = page.getByRole("dialog", { name: "Cockpit-Handbuch" });
  await expect(landscapeGuide).toBeVisible();
  expect(await landscapeGuide.evaluate((element) =>
    element.scrollWidth <= element.clientWidth && element.getBoundingClientRect().height <= 390,
  )).toBe(true);
});
