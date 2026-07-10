// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — built-cockpit durable receipt and operator-audit acceptance

import { expect, test } from "@playwright/test";

const bearer = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
if (bearer === undefined || bearer === "") {
  throw new Error("SYNAPSE_COCKPIT_E2E_TOKEN is required by the browser gate");
}

test("the built cockpit renders store-backed receipts and operator audit", async ({ page }) => {
  await page.goto("/cockpit/");
  const receiptResponse = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/receipts.json" && response.status() === 200,
  );
  const actionResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/operator-actions.json" && response.status() === 200,
  );
  await page.getByLabel("Dashboard bearer token").fill(bearer);
  await page.getByRole("button", { name: "unlock cockpit" }).click();
  await expect(page.getByText("live", { exact: true })).toBeVisible();
  const [receiptsLoaded, actionsLoaded] = await Promise.all([receiptResponse, actionResponse]);
  expect(receiptsLoaded.request().headers()["authorization"]).toBe(`Bearer ${bearer}`);
  expect(actionsLoaded.request().headers()["authorization"]).toBe(`Bearer ${bearer}`);

  const target = `cockpit-e2e-audit-${Date.now().toString(36)}`;
  await page.keyboard.press("Control+k");
  await page.getByRole("option", { name: "operator: send a message…" }).click();
  await page.getByLabel("Message recipient").fill(target);
  await page.getByLabel("Message text").fill("durable audit browser acceptance");
  await page.getByRole("button", { name: "send" }).click();
  await expect(page.getByText(/relayed, not delivered/u)).toBeVisible();
  await page.keyboard.press("Escape");

  await page.getByRole("tab", { name: "audit" }).click();
  const receiptPanel = page.getByLabel("Universal receipts");
  await expect(receiptPanel).toContainText(target, { timeout: 8_000 });
  await expect(receiptPanel).toContainText("undelivered");
  await expect(receiptPanel).toContainText("operator:cockpit-e2e");

  const actionPanel = page.getByLabel("Governed operator actions");
  await expect(actionPanel).toContainText("cockpit-e2e-audit-seed");
  await expect(actionPanel).toContainText("release");
  await expect(actionPanel).toContainText("applied");
  await expect(actionPanel).toContainText("operator:cockpit-e2e-seed");
  await expect(page.getByLabel("Receipt and operator audit")).toContainText("durable store");
});
