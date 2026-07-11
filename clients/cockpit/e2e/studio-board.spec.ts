// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — real Chromium Studio board-column acceptance

import { expect, test, type APIRequestContext } from "@playwright/test";

const bearer = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
if (bearer === undefined || bearer === "") {
  throw new Error("SYNAPSE_COCKPIT_E2E_TOKEN is required by the browser gate");
}

const headers = { Authorization: `Bearer ${bearer}` };

async function declareTask(
  request: APIRequestContext,
  id: string,
  title: string,
  dependsOn: readonly string[] = [],
): Promise<void> {
  const response = await request.post("/task", {
    headers,
    data: { id, title, depends_on: dependsOn },
  });
  expect(response.status()).toBe(200);
  expect(await response.json()).toEqual(expect.objectContaining({ ok: true, status: "accepted" }));
}

async function updateTask(
  request: APIRequestContext,
  id: string,
  status: string,
): Promise<void> {
  const response = await request.post("/task/update", {
    headers,
    data: { id, status, note: "Studio board browser acceptance" },
  });
  expect(response.status()).toBe(200);
  expect(await response.json()).toEqual(expect.objectContaining({ ok: true, status: "accepted" }));
}

test("the protected Studio renders exact read-only columns on desktop and mobile", async ({
  page,
  request,
}) => {
  const suffix = Date.now().toString(36);
  const openId = `studio-open-${suffix}`;
  const workingId = `studio-working-${suffix}`;
  const blockedId = `studio-blocked-${suffix}`;
  const doneId = `studio-done-${suffix}`;
  const payload = `<img id="studio-pwn" onerror="window.__studioPwned=1">\"'`;

  await declareTask(request, openId, payload);
  await declareTask(request, workingId, "Working in Chromium");
  await updateTask(request, workingId, "in_progress");
  await declareTask(request, blockedId, "Blocked in Chromium", [workingId]);
  await updateTask(request, blockedId, "blocked");
  await declareTask(request, doneId, "Closed in Chromium");
  await updateTask(request, doneId, "done");

  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.setExtraHTTPHeaders(headers);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 1440, height: 1000 });
  const snapshot = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/studio.json" && response.status() === 200,
  );
  await page.goto("/studio/command");
  expect((await snapshot).request().headers()["authorization"]).toBe(`Bearer ${bearer}`);

  const board = page.locator("#cc-board-columns");
  await expect(board.locator('.syn-board-column[data-column="open"]')).toContainText(openId);
  await expect(board.locator('.syn-board-column[data-column="working"]')).toContainText(workingId);
  await expect(board.locator('.syn-board-column[data-column="blocked"]')).toContainText(blockedId);
  await expect(board.locator('.syn-board-column[data-column="closed"]')).toContainText(doneId);
  await expect(board).toContainText(payload);
  await expect(page.locator("#studio-pwn, img[onerror], [onclick]")).toHaveCount(0);
  expect(await page.evaluate(() => (window as typeof window & { __studioPwned?: number }).__studioPwned))
    .toBeUndefined();
  await expect(page.getByText("Read-only projection; actions remain hub-enforced.")).toBeVisible();
  await expect(page.locator(".cc-shell button, .cc-shell input, .cc-shell form")).toHaveCount(0);
  await expect(page.locator(".cc-sweep")).toHaveCount(0);
  expect(await board.evaluate((element) => getComputedStyle(element).gridAutoFlow)).toBe("column");

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await board.evaluate((element) => getComputedStyle(element).gridAutoFlow)).toBe("row");
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBe(true);
  expect(pageErrors).toEqual([]);
});
