// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — production communication workbench acceptance

import { expect, test, type Page } from "@playwright/test";

import { formatMessage, type MessageKey } from "../src/lib/i18n";

function fr(
  key: MessageKey,
  values: Readonly<Record<string, string | number>> = {},
): string {
  return formatMessage("fr", key, values);
}

function regexEscape(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
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
  await page.getByLabel("Dashboard bearer token").fill(bearer);
  await page.getByRole("button", { name: "unlock cockpit" }).click();
  await expect(page.getByText("operator · operator", { exact: true })).toBeVisible();
}

test("filters a durable route, restores its URL, and responds to exact evidence", async ({ page }) => {
  await unlock(page);
  await page.getByLabel("Interface language").selectOption("fr");
  await expect(page.locator("html")).toHaveAttribute("lang", "fr");
  const target = "cockpit-e2e-filter-target";
  const relay = await page.request.post("/message", {
    headers: { Authorization: `Bearer ${bearer}` },
    data: { to: target, text: "communication evidence probe" },
  });
  expect(relay.status()).toBe(200);

  await page.getByRole("tab", { name: fr("tab.fleet") }).click();
  await page.getByLabel(fr("fleet.filters.query")).fill(target);
  await page.getByLabel(fr("fleet.filters.health")).selectOption("failed");
  await expect(page).toHaveURL(/panel=fleet/u);
  await expect(page).toHaveURL(/comm=cockpit-e2e-filter-target/u);
  await expect(page).toHaveURL(/delivery=failed/u);
  const resultsPattern = regexEscape(fr("fleet.filters.results", {
    shown: 1,
    total: "__TOTAL__",
    messages: 1,
  })).replace("__TOTAL__", "\\d+");
  await expect(page.getByText(new RegExp(resultsPattern, "u"))).toBeVisible();

  const routePattern = regexEscape(fr("fleet.web.selectPriority", {
    source: "__SOURCE__",
    target,
    messageCount: "1 message",
  })).replace("__SOURCE__", ".*");
  await page.getByRole("button", {
    name: new RegExp(routePattern, "u"),
  }).click();
  const detail = page.getByRole("complementary", { name: fr("fleet.detail.linkAria") });
  await expect(detail).toContainText("communication evidence probe");
  await expect(detail.getByText(fr("fleet.detail.transportReceipt"))).toBeVisible();
  await expect(detail.getByText(fr("fleet.detail.correlated"))).toBeVisible();
  await expect(detail.getByText(fr("fleet.detail.noneRetained"))).toBeVisible();

  const messageSeqText = await detail.locator(".fleet-message__meta b").first().innerText();
  const messageSeq = Number.parseInt(messageSeqText.replace("#", ""), 10);
  expect(Number.isSafeInteger(messageSeq)).toBe(true);
  const responseRequest = page.waitForRequest((request) => request.url().endsWith("/message/respond"));
  await detail.getByLabel(fr("fleet.detail.respondTo", { seq: messageSeq })).selectOption("acknowledged");
  await detail.getByLabel(fr("fleet.detail.optionalNote")).fill("Operator reviewed exact sequence.");
  await detail.getByRole("button", { name: fr("fleet.detail.sendResponse") }).click();
  const request = await responseRequest;
  expect(request.postDataJSON()).toMatchObject({
    message_seq: messageSeq,
    status: "acknowledged",
    note: "Operator reviewed exact sequence.",
  });
  await expect(detail.getByText(/semantic response recorded/iu)).toBeVisible();

  await page.reload();
  await expect(page.getByLabel(fr("fleet.filters.query"))).toHaveValue(target);
  await expect(page.getByLabel(fr("fleet.filters.health"))).toHaveValue("failed");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
