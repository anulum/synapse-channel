// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — real-browser principal rendering and capability isolation

import { expect, test, type Page } from "@playwright/test";

type DashboardRole = "viewer" | "operator" | "admin";

function requiredToken(name: string): string {
  const value = process.env[name];
  if (value === undefined || value === "") throw new Error(`${name} is required by the browser gate`);
  return value;
}

const tokens = {
  viewer: requiredToken("SYNAPSE_COCKPIT_E2E_VIEWER_TOKEN"),
  operator: requiredToken("SYNAPSE_COCKPIT_E2E_TOKEN"),
  admin: requiredToken("SYNAPSE_COCKPIT_E2E_ADMIN_TOKEN"),
} as const;

async function unlock(page: Page, role: DashboardRole): Promise<void> {
  await page.goto("/cockpit/");
  await page.getByLabel("Dashboard bearer token").fill(tokens[role]);
  await page.getByRole("button", { name: "unlock cockpit" }).click();
  await expect(page.getByText(`${role} · ${role}`, { exact: true })).toBeVisible();
}

async function changeAccess(page: Page, role: DashboardRole): Promise<void> {
  await page.getByRole("button", { name: "change access" }).click();
  await page.getByLabel("Dashboard bearer token").fill(tokens[role]);
  await page.getByRole("button", { name: "unlock cockpit" }).click();
  await expect(page.getByText(`${role} · ${role}`, { exact: true })).toBeVisible();
}

async function expectRoleAtWidth(page: Page, role: DashboardRole, width: number): Promise<void> {
  await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
  await expect(page.getByText(`${role} · ${role}`, { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Open command palette" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

test("viewer tampering cannot reach writes; operator/admin expose exactly shipped controls", async ({ page }) => {
  await unlock(page, "viewer");
  for (const width of [1440, 390]) await expectRoleAtWidth(page, "viewer", width);
  await page.getByRole("button", { name: "Open command palette" }).click();
  await expect(page.locator(".palette__item--write")).toHaveCount(0);
  await page.getByLabel("Search commands").fill("operator");
  await expect(page.getByText("no command matches")).toBeVisible();
  await page.keyboard.press("Escape");

  const viewerPost = await page.evaluate(async (bearer) => {
    const document: Document = globalThis.document;
    const fake = document.createElement("button");
    fake.id = "forged-operator-control";
    fake.textContent = "forged operator action";
    document.body.append(fake);
    const response = await fetch("/message", {
      method: "POST",
      headers: { Authorization: `Bearer ${bearer}`, "Content-Type": "application/json" },
      body: JSON.stringify({ to: "x", text: "forged" }),
    });
    return { status: response.status, body: await response.text() };
  }, tokens.viewer);
  expect(viewerPost).toEqual({ status: 403, body: "dashboard capability denied\n" });

  for (const role of ["operator", "admin"] as const) {
    await changeAccess(page, role);
    for (const width of [1440, 390]) await expectRoleAtWidth(page, role, width);
    await page.getByRole("button", { name: "Open command palette" }).click();
    await expect(page.locator(".palette__item--write")).toHaveCount(3);
    await expect(page.getByRole("option", { name: "operator: send a message…" })).toBeVisible();
    await expect(page.getByRole("option", { name: "operator: declare a task…" })).toBeVisible();
    await expect(page.getByRole("option", { name: "operator: update a task…" })).toBeVisible();
    await expect(page.getByText(/admin:/u)).toHaveCount(0);
    await page.keyboard.press("Escape");
    const relay = await page.request.post("/message", {
      headers: { Authorization: `Bearer ${tokens[role]}` },
      data: { to: `cockpit-e2e-absent-${role}`, text: `${role} identity audit` },
    });
    expect(relay.status()).toBe(200);
  }

  const receipts = await page.request.get("/receipts.json?since=0&limit=100", {
    headers: { Authorization: `Bearer ${tokens.admin}` },
  });
  expect(receipts.status()).toBe(200);
  const document = (await receipts.json()) as {
    readonly receipts: readonly { readonly actor?: unknown }[];
  };
  const operators = document.receipts.map((receipt) => receipt.actor);
  expect(operators).toEqual(
    expect.arrayContaining(["operator:cockpit-e2e", "operator:cockpit-e2e-admin"]),
  );
});

test("a live capability downgrade closes writes, restores focus, and announces", async ({ page }) => {
  await unlock(page, "operator");
  const trigger = page.getByRole("button", { name: "Open command palette" });
  await trigger.click();
  await expect(page.locator(".palette__item--write")).toHaveCount(3);
  await page.route("**/dashboard-access.json", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        version: 1,
        principal: "operator",
        role: "operator",
        capabilities: {
          read: true,
          message_send: false,
          task_declare: false,
          task_update: false,
        },
        operator_armed: true,
        trust_boundary: "presentation hints only; HTTP and hub policy enforce writes",
      }),
    });
  });
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByLabel("Search commands")).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await expect(page.getByRole("status")).toContainText(
    "Dashboard access changed; write controls were removed.",
  );
  await trigger.click();
  await expect(page.locator(".palette__item--write")).toHaveCount(0);
});
