// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — built-cockpit bearer and operator browser acceptance

import { expect, test } from "@playwright/test";

const bearer = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
if (bearer === undefined || bearer === "") {
  throw new Error("SYNAPSE_COCKPIT_E2E_TOKEN is required by the browser gate");
}

test("the production cockpit unlocks, governs writes, and locks on 401", async ({ page }) => {
  await page.goto("/cockpit/");
  await expect(page.getByRole("heading", { name: "Unlock cockpit" })).toBeVisible();
  expect(page.url()).not.toContain(bearer);

  await page.getByLabel("Dashboard bearer token").fill("wrong-e2e-bearer");
  await page.getByRole("button", { name: "unlock cockpit" }).click();
  await expect(page.getByRole("alert")).toContainText("refused");
  await page.keyboard.press("Control+k");
  await expect(page.getByRole("option", { name: "operator: declare a task…" })).toHaveCount(0);
  expect(await page.evaluate(() => sessionStorage.getItem("synapse-cockpit-bearer"))).toBeNull();

  await page.getByLabel("Dashboard bearer token").fill(bearer);
  await page.getByRole("button", { name: "unlock cockpit" }).click();
  await expect(page.getByText("live", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("synapse-cockpit-bearer"))).toBe(bearer);
  expect(await page.evaluate(() => localStorage.getItem("synapse-cockpit-bearer"))).toBeNull();

  const resourceUrls = await page.evaluate(() =>
    performance.getEntriesByType("resource").map((entry) => entry.name),
  );
  expect(resourceUrls.join("\n")).not.toContain(bearer);
  expect(await page.content()).not.toContain(bearer);

  await page.keyboard.press("Control+k");
  await page.getByRole("option", { name: "operator: send a message…" }).click();
  await page.getByLabel("Message recipient").fill("cockpit-e2e-absent-recipient");
  await page.getByLabel("Message text").fill("production browser acceptance");
  const messageRequest = page.waitForRequest(
    (request) => request.method() === "POST" && new URL(request.url()).pathname === "/message",
  );
  await page.getByRole("button", { name: "send" }).click();
  const request = await messageRequest;
  expect(request.headers()["authorization"]).toBe(`Bearer ${bearer}`);
  await expect(page.getByText(/relayed, not delivered/u)).toBeVisible();

  const suffix = Date.now().toString(36);
  const parentTask = `cockpit-e2e-parent-${suffix}`;
  const childTask = `cockpit-e2e-child-${suffix}`;
  await page.getByRole("button", { name: "back" }).click();
  await page.getByRole("option", { name: "operator: declare a task…" }).click();
  await page.getByLabel("Task id").fill(parentTask);
  await page.getByLabel("Task title").fill("Cockpit browser parent");
  const parentRequest = page.waitForRequest(
    (candidate) => candidate.method() === "POST" && new URL(candidate.url()).pathname === "/task",
  );
  await page.getByRole("button", { name: "declare task" }).click();
  const parent = await parentRequest;
  expect(parent.headers()["authorization"]).toBe(`Bearer ${bearer}`);
  expect(parent.postDataJSON()).toEqual({
    id: parentTask,
    title: "Cockpit browser parent",
    depends_on: [],
  });
  await expect(page.locator(".palette__outcome")).toContainText("accepted");

  await page.getByRole("button", { name: "back" }).click();
  await page.getByRole("option", { name: "operator: declare a task…" }).click();
  await page.getByLabel("Task id").fill(childTask);
  await page.getByLabel("Task title").fill("Cockpit browser dependent");
  await page.getByLabel(/Dependencies/u).fill(parentTask);
  const childRequest = page.waitForRequest(
    (candidate) => candidate.method() === "POST" && new URL(candidate.url()).pathname === "/task",
  );
  await page.getByRole("button", { name: "declare task" }).click();
  const child = await childRequest;
  expect(child.headers()["authorization"]).toBe(`Bearer ${bearer}`);
  expect(child.postDataJSON()).toEqual({
    id: childTask,
    title: "Cockpit browser dependent",
    depends_on: [parentTask],
  });
  await expect(page.locator(".palette__outcome")).toContainText("accepted");

  await page.getByRole("button", { name: "back" }).click();
  await page.getByRole("option", { name: "operator: update a task…" }).click();
  await expect(page.locator(`#operator-task-ids option[value="${childTask}"]`)).toHaveCount(1);
  await page.getByLabel("Task id").fill(childTask);
  await page.getByLabel(/Task status/u).fill("done");
  await page.getByLabel(/Progress note/u).fill("production browser acceptance");
  const updateRequest = page.waitForRequest(
    (candidate) =>
      candidate.method() === "POST" && new URL(candidate.url()).pathname === "/task/update",
  );
  await page.getByRole("button", { name: "update task" }).click();
  const update = await updateRequest;
  expect(update.headers()["authorization"]).toBe(`Bearer ${bearer}`);
  expect(update.postDataJSON()).toEqual({
    id: childTask,
    status: "done",
    note: "production browser acceptance",
  });
  await expect(page.locator(".palette__outcome")).toContainText("accepted");
  await page.keyboard.press("Escape");
  await expect(page.locator(".board-row", { hasText: childTask })).toContainText("done");

  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
  });
  const cachedRequests = await page.evaluate(async () => {
    const records: { url: string; authorization: string | null }[] = [];
    for (const cacheName of await caches.keys()) {
      const cache = await caches.open(cacheName);
      for (const cached of await cache.keys()) {
        records.push({ url: cached.url, authorization: cached.headers.get("Authorization") });
      }
    }
    return records;
  });
  expect(cachedRequests.length).toBeGreaterThan(0);
  expect(cachedRequests.every((cached) => cached.authorization === null)).toBe(true);
  expect(JSON.stringify(cachedRequests)).not.toContain(bearer);

  await page.route("**/snapshot.json*", async (route) => {
    await route.fulfill({ status: 401, contentType: "text/plain", body: "revoked\n" });
  });
  await expect(page.getByRole("heading", { name: "Unlock cockpit" })).toBeVisible({
    timeout: 8_000,
  });
  expect(await page.evaluate(() => sessionStorage.getItem("synapse-cockpit-bearer"))).toBeNull();
  await expect(page.getByText("agents online", { exact: true })).toHaveCount(0);
});
