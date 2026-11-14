// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the dashboard's root is the cockpit's unlock screen

import { expect, test } from "@playwright/test";

const bearer = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
if (!bearer) throw new Error("SYNAPSE_COCKPIT_E2E_TOKEN is required");

test("a person who opens the root reaches the cockpit, unlocks it and keeps the language", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/cockpit\/$/u);
  await page.getByLabel("Dashboard bearer token").fill(bearer);
  await page.getByRole("button", { name: "unlock cockpit" }).click();
  await expect(page.getByRole("banner").getByText("live", { exact: true })).toBeVisible();

  await page.goto("/?lang=sk");
  await expect(page).toHaveURL(/\/cockpit\/\?lang=sk$/u);
  // The query reaches the cockpit: its interface is Slovak.
  await expect(page.getByRole("combobox", { name: "Jazyk rozhrania" })).toHaveValue("sk");
});
