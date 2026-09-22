// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — production cockpit attention feed journey

import { expect, test } from "@playwright/test";

test("built cockpit renders an overdue local approval without its task body", async ({ page }) => {
  const bearer = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
  if (bearer === undefined || bearer === "") throw new Error("browser bearer unavailable");
  await page.goto("/cockpit/?lang=en#panel=attention");
  await page.getByLabel("Dashboard bearer token").fill(bearer);
  await page.getByRole("button", { name: "unlock cockpit" }).click();
  await page.getByRole("tab", { name: "attention" }).click();
  const queue = page.getByRole("region", { name: "Fleet attention queue" });
  await expect(queue.getByText("attention-e2e-task")).toBeVisible();
  await expect(queue.getByText(/Review overdue/)).toBeVisible();
  await expect(queue.getByText("critical", { exact: true })).toBeVisible();
  await expect(queue).not.toContainText("private task body");
  await expect(queue).not.toContainText("Local attention observer is late");
});
