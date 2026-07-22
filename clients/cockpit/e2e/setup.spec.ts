// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — production setup-assistant safety and accessibility gate

import { expect, test, type Page } from "@playwright/test";
import axe from "axe-core";

interface AxeViolation {
  readonly id: string;
  readonly impact: string | null;
  readonly nodes: readonly { readonly html: string }[];
}

interface AxeWindow extends Window {
  readonly axe: {
    run(root: Document): Promise<{ readonly violations: readonly AxeViolation[] }>;
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
const dashboardPort = process.env["SYNAPSE_COCKPIT_E2E_DASHBOARD_PORT"] ?? "18765";

async function unlock(page: Page): Promise<void> {
  await page.getByLabel("Dashboard bearer token").fill(bearer);
  await page.getByRole("button", { name: "unlock cockpit" }).click();
  await expect(page.getByRole("banner").getByText("live", { exact: true })).toBeVisible();
}

test("the built setup assistant remains read-only, secret-free and operable in both themes", async ({ page, context }) => {
  const consoleLines: string[] = [];
  page.on("console", (message) => consoleLines.push(message.text()));
  await page.addInitScript({ content: axe.source });
  await context.grantPermissions(["clipboard-read", "clipboard-write"], {
    origin: `http://127.0.0.1:${dashboardPort}`,
  });
  await page.goto("/cockpit/?lang=en#panel=attention");
  await unlock(page);
  const switchToDark = page.getByRole("button", { name: "Switch to dark theme" });
  if (await switchToDark.isVisible()) await switchToDark.click();
  await expect(page.getByRole("button", { name: "Switch to light theme" })).toBeVisible();
  const startingUrl = page.url();
  await page.evaluate(async () => navigator.clipboard.writeText("setup-clipboard-sentinel"));

  await page.getByRole("button", { name: "Open local setup assistant" }).click();
  const assistant = page.getByRole("dialog", { name: "Setup assistant" });
  await expect(assistant).toBeVisible();
  await expect(assistant).toContainText("Local setup · read only");
  await expect(assistant).not.toContainText(bearer);
  expect(page.url()).toBe(startingUrl);
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("setup-clipboard-sentinel");

  const darkResult = await page.evaluate(async () =>
    (window as unknown as AxeWindow).axe.run(document),
  );
  expect(darkResult.violations, JSON.stringify(darkResult.violations)).toEqual([]);

  await assistant.getByRole("button", { name: "Profile" }).click();
  await assistant.getByLabel("Add durable evidence placeholders").check();
  await assistant.getByLabel("Add protected dashboard policy placeholder").check();
  await assistant.getByRole("button", { name: "Commands" }).click();
  const previews = assistant.locator("code");
  await expect(previews).toHaveCount(2);
  await expect(previews.nth(0)).toContainText("--host 127.0.0.1");
  await expect(previews.nth(1)).toContainText("<OWNER_ONLY_ACCESS_POLICY_PATH>");
  await expect(previews.nth(1)).not.toContainText("--dashboard-token");
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("setup-clipboard-sentinel");

  await assistant.getByRole("button", { name: "Copy command" }).first().click();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toBe("synapse hub --host 127.0.0.1 --port 8876 --metrics --db <HUB_DB_PATH>");
  expect(copied).not.toContain(bearer);
  expect(copied).not.toContain("--token");
  expect(page.url()).toBe(startingUrl);
  expect(await page.evaluate(
    (secret) => Object.keys(localStorage).some((key) => (localStorage.getItem(key) ?? "").includes(secret)),
    bearer,
  )).toBe(false);
  expect(consoleLines.some((line) => line.includes(bearer))).toBe(false);

  await assistant.getByRole("button", { name: "Close setup assistant" }).click();
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Open local setup assistant" }).click();
  const narrowAssistant = page.getByRole("dialog", { name: "Setup assistant" });
  const box = await narrowAssistant.boundingBox();
  expect(box?.x).toBe(0);
  expect(box?.width).toBe(390);
  expect(await narrowAssistant.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  const lightResult = await page.evaluate(async () =>
    (window as unknown as AxeWindow).axe.run(document),
  );
  expect(lightResult.violations, JSON.stringify(lightResult.violations)).toEqual([]);
});
