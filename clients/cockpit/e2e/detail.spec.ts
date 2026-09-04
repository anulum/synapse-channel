// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — keyboard agent inspection browser acceptance

import { expect, test } from "@playwright/test";
import axe from "axe-core";

for (const width of [1440, 390]) {
  test(`roster inspection supports keyboard and modal focus at ${width}px`, async ({ page }) => {
    const bearer = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
    if (!bearer) throw new Error("SYNAPSE_COCKPIT_E2E_TOKEN is required");
    await page.addInitScript({ content: axe.source });
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/cockpit/");
    await page.getByLabel("Dashboard bearer token").fill(bearer);
    await page.getByRole("button", { name: "unlock cockpit" }).click();
    if (width === 390) await page.getByRole("button", { name: "roster", exact: true }).click();
    const roster = page.getByRole("region", { name: "Fleet roster" });
    const inspect = roster.getByRole("button", { name: "Inspect agent cockpit-e2e-dashboard", exact: true });
    await expect(inspect).toBeVisible();
    await roster.locator(".panel__body").focus();
    await page.keyboard.press("Tab");
    await expect(inspect).toBeFocused();
    const dialog = page.getByRole("dialog", { name: "Agent cockpit-e2e-dashboard", exact: true });
    for (const key of ["Enter", "Space"]) {
      await page.keyboard.press(key);
      await expect(dialog).toBeVisible();
      const close = dialog.getByRole("button", { name: "Close the drawer" });
      const filter = dialog.getByRole("button", { name: "filter log", exact: true });
      await expect(close).toBeFocused();
      await page.keyboard.press("Shift+Tab");
      await expect(filter).toBeFocused();
      await page.keyboard.press("Tab");
      await expect(close).toBeFocused();
      await expect(page.getByRole("banner", { includeHidden: true })).toHaveAttribute("inert", "");
      await page.keyboard.press("Escape");
      await expect(dialog).toHaveCount(0);
      await expect(inspect).toBeFocused();
      await expect(page.getByRole("banner")).not.toHaveAttribute("inert");
    }
    await inspect.click();
    await expect(dialog).toBeVisible();
    const violations = await page.evaluate(async () => {
      const audit = window as typeof window & {
        axe: { run(root: Document): Promise<{ violations: unknown[] }> };
      };
      return (await audit.axe.run(document)).violations;
    });
    expect(violations).toEqual([]);
    await page.screenshot({ path: test.info().outputPath("agent-detail.png") });
    await dialog.getByRole("button", { name: "Close the drawer" }).click();
    await expect(inspect).toBeFocused();
  });
}
