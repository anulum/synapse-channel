// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — production communication workbench acceptance

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
  await page.goto("/cockpit/");
  await page.getByLabel("Dashboard bearer token").fill(bearer);
  await page.getByRole("button", { name: "unlock cockpit" }).click();
  await expect(page.getByText("operator · operator", { exact: true })).toBeVisible();
}

test("filters a durable route, restores its URL, and responds to exact evidence", async ({ page }) => {
  await unlock(page);
  const target = "cockpit-e2e-filter-target";
  const relay = await page.request.post("/message", {
    headers: { Authorization: `Bearer ${bearer}` },
    data: { to: target, text: "communication evidence probe" },
  });
  expect(relay.status()).toBe(200);

  await page.getByRole("tab", { name: "fleet" }).click();
  await page.getByLabel("identity or project").fill(target);
  await page.getByLabel("delivery health").selectOption("failed");
  await expect(page).toHaveURL(/panel=fleet/u);
  await expect(page).toHaveURL(/comm=cockpit-e2e-filter-target/u);
  await expect(page).toHaveURL(/delivery=failed/u);
  await expect(page.getByText(/1 of \d+ routes · 1 messages/u)).toBeVisible();

  await page.getByRole("button", {
    name: new RegExp(`Select priority route .* to ${target}: 1 message`, "u"),
  }).click();
  const detail = page.getByRole("complementary", { name: "Communication detail" });
  await expect(detail).toContainText("communication evidence probe");
  await expect(detail.getByText("2 · transport receipt")).toBeVisible();
  await expect(detail.getByText("correlated by exact message sequence")).toBeVisible();
  await expect(detail.getByText("none retained")).toBeVisible();

  const messageSeqText = await detail.locator(".fleet-message__meta b").first().innerText();
  const messageSeq = Number.parseInt(messageSeqText.replace("#", ""), 10);
  expect(Number.isSafeInteger(messageSeq)).toBe(true);
  const responseRequest = page.waitForRequest((request) => request.url().endsWith("/message/respond"));
  await detail.getByLabel(new RegExp(`respond to #${messageSeq}`, "u")).selectOption("acknowledged");
  await detail.getByLabel("optional note").fill("Operator reviewed exact sequence.");
  await detail.getByRole("button", { name: "send response" }).click();
  const request = await responseRequest;
  expect(request.postDataJSON()).toMatchObject({
    message_seq: messageSeq,
    status: "acknowledged",
    note: "Operator reviewed exact sequence.",
  });
  await expect(detail.getByText(/semantic response recorded/iu)).toBeVisible();

  await page.reload();
  await expect(page.getByLabel("identity or project")).toHaveValue(target);
  await expect(page.getByLabel("delivery health")).toHaveValue("failed");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
