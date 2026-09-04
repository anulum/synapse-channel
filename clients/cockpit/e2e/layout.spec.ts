// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — built cockpit panel containment acceptance

import { expect, test } from "@playwright/test";

const bearer = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
if (!bearer) throw new Error("SYNAPSE_COCKPIT_E2E_TOKEN is required");

for (const width of [1100, 1280, 1440, 1920]) {
  test(`panels and header controls fit a ${width}px viewport`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/cockpit/");
    await page.getByLabel("Dashboard bearer token").fill(bearer);
    await page.getByRole("button", { name: "unlock cockpit" }).click();
    const banner = page.getByRole("banner");
    await expect(banner.getByText("live", { exact: true })).toBeVisible();
    await page.evaluate(() => document.fonts.ready);

    for (const language of ["EN", "SK", "DE", "ES", "FR"]) {
      await banner.getByRole("combobox").first().selectOption({ label: language });
      for (let density = 0; density < 2; density += 1) {
        await test.step(`${language}/density-${density}`, async () => {
          if (density === 0) await expect(page.locator("html")).not.toHaveAttribute("data-density");
          else await expect(page.locator("html")).toHaveAttribute("data-density", "compact");
          await expect.poll(async () => page.evaluate(() => {
            const header = document.querySelector("header")!.getBoundingClientRect();
            const targets = document.querySelectorAll(
              "header, header button, header select, header input, section[aria-label]",
            );
            return Array.from(targets).flatMap((element) => {
              const rect = element.getBoundingClientRect();
              if (rect.width === 0 || rect.height === 0) return [];
              const outsideHeader = element.closest("header") !== null
                && (rect.top < header.top - 1 || rect.bottom > header.bottom + 1);
              return rect.left < -1 || rect.right > innerWidth + 1 || outsideHeader
                ? [{
                    label: element.getAttribute("aria-label") ?? element.textContent,
                    left: rect.left,
                    right: rect.right,
                    outsideHeader,
                  }]
                : [];
            });
          })).toEqual([]);
          for (const name of ["Risk rail", "Findings stream", "Task board", "Fleet roster"]) {
            await expect(page.getByRole("region", { name, exact: true })).toBeVisible();
          }
          if (language === "EN" && density === 0) {
            const screenshot = test.info().outputPath("contained-deck.png");
            await page.screenshot({ path: screenshot });
            await test.info().attach("contained-deck", {
              path: screenshot,
              contentType: "image/png",
            });
          }
        });
        await banner.getByRole("button", {
          name: /Toggle display density|Prepnúť hustotu zobrazenia|Anzeigedichte umschalten|Cambiar la densidad de visualización|Changer la densité d’affichage/u,
        }).click();
      }
    }
  });
}
