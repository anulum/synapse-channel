// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — production cockpit accessibility browser acceptance

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

async function unlock(page: Page): Promise<void> {
  await page.goto("/cockpit/");
  const input = page.getByLabel("Dashboard bearer token");
  if (await input.isVisible()) {
    await input.fill(bearer);
    await page.getByRole("button", { name: "unlock cockpit" }).click();
  }
  await expect(page.getByRole("banner").getByText("live", { exact: true })).toBeVisible();
}

async function waitForThemeTransition(page: Page): Promise<void> {
  await expect
    .poll(() =>
      page.locator(".roster-row").first().evaluate((row) => {
        const probe = document.createElement("span");
        probe.style.color = "var(--panel-2)";
        document.body.append(probe);
        const expected = getComputedStyle(probe).color;
        probe.remove();
        return getComputedStyle(row).backgroundColor === expected;
      }),
    )
    .toBe(true);
}

test("the built live deck has zero axe violations in both themes and viewports", async ({ page }) => {
  // Install through Playwright's pre-document hook. Unlike an inline <script>,
  // this preserves the production `script-src 'self'` policy under test.
  await page.addInitScript({ content: axe.source });
  await unlock(page);

  const viewports = [
    { name: "desktop", width: 1440, height: 1000 },
    { name: "phone", width: 390, height: 844 },
  ] as const;
  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    for (const theme of ["dark", "light"] as const) {
      const current = await page.locator("html").getAttribute("data-theme");
      if ((current === "light" ? "light" : "dark") !== theme) {
        await page.getByRole("button", { name: /Switch to (?:dark|light) theme/u }).click();
      }
      await waitForThemeTransition(page);
      const result = await page.evaluate(async () =>
        (window as unknown as AxeWindow).axe.run(document),
      );
      expect(result.violations, `${viewport.name}/${theme}: ${JSON.stringify(result.violations)}`).toEqual([]);
    }
  }

  await page.setViewportSize(viewports[0]);
  await page.getByRole("tab", { name: "incident" }).click();
  await page.getByLabel("Incident title").fill("Accessibility incident review");
  await page.getByRole("button", { name: /continue to evidence/u }).click();
  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    for (const theme of ["dark", "light"] as const) {
      const current = await page.locator("html").getAttribute("data-theme");
      if ((current === "light" ? "light" : "dark") !== theme) {
        await page.getByRole("button", { name: /Switch to (?:dark|light) theme/u }).click();
      }
      await waitForThemeTransition(page);
      const result = await page.evaluate(async () =>
        (window as unknown as AxeWindow).axe.run(document),
      );
      expect(
        result.violations,
        `incident ${viewport.name}/${theme}: ${JSON.stringify(result.violations)}`,
      ).toEqual([]);
    }
  }

  await page.setViewportSize(viewports[0]);
  await page.getByRole("tab", { name: "fleet" }).click();
  await page.getByLabel("identity or project").fill("cockpit-e2e");
  await page.getByLabel("delivery health").selectOption("deferred");
  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    for (const theme of ["dark", "light"] as const) {
      const current = await page.locator("html").getAttribute("data-theme");
      if ((current === "light" ? "light" : "dark") !== theme) {
        await page.getByRole("button", { name: /Switch to (?:dark|light) theme/u }).click();
      }
      await waitForThemeTransition(page);
      const result = await page.evaluate(async () =>
        (window as unknown as AxeWindow).axe.run(document),
      );
      expect(
        result.violations,
        `communications ${viewport.name}/${theme}: ${JSON.stringify(result.violations)}`,
      ).toEqual([]);
    }
  }

  await page.setViewportSize(viewports[0]);
  await page.keyboard.press("?");
  await expect(page.getByRole("dialog", { name: "Cockpit guide" })).toBeVisible();
  const guideResult = await page.evaluate(async () =>
    (window as unknown as AxeWindow).axe.run(document),
  );
  expect(
    guideResult.violations,
    `guide desktop: ${JSON.stringify(guideResult.violations)}`,
  ).toEqual([]);
});
