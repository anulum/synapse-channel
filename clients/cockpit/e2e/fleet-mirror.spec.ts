// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — the Fleet mirror as a scope: offered to an observer only, advisory, withdrawn with its grant

import { readFileSync, writeFileSync } from "node:fs";

import { expect, test, type Page } from "@playwright/test";
import axe from "axe-core";

import { startHarness, stop, type Started } from "./fleetHarness";

const operator = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
const viewer = process.env["SYNAPSE_COCKPIT_E2E_VIEWER_TOKEN"];
if (!operator || !viewer) throw new Error("the operator and viewer e2e tokens are required");

const SCOPE_LABEL = "Scope: one project's part of the fleet";
const BAND = "advisory · Fleet mirror fleet-e2e";
/** What the Fleet scope may read: the mirror, its grant, and the login's own access check. */
const MIRROR_PATHS = new Set(["/fleet-observed.json", "/fleet-observed-access.json", "/dashboard-access.json"]);
/**
 * How long the network is watched once the switch to the Fleet scope has committed: past the 20 s
 * after which this hub's other feeds start without a live stream (AUXILIARY_FEED_START_FALLBACK_MS),
 * had the scope left them open, and past two more reads of the mirror (every 10 s).
 */
const WATCHED_MS = 25_000;

interface AxeWindow extends Window {
  readonly axe: {
    run(context: { readonly include: readonly (readonly string[])[] }): Promise<{
      readonly violations: readonly { readonly id: string }[];
    }>;
  };
}

const region = (page: Page, name: string) => page.getByRole("region", { name, exact: true });

async function unlock(page: Page, url: string, bearer: string): Promise<void> {
  await page.goto(url);
  await page.getByLabel("Dashboard bearer token").fill(bearer);
  await page.getByRole("button", { name: "unlock cockpit" }).click();
  await expect(page.getByRole("banner").getByText("live", { exact: true })).toBeVisible();
}

async function writeCount(page: Page): Promise<number> {
  await page.getByRole("button", { name: "commands Ctrl K: Open command palette" }).click();
  const count = await page.locator(".palette__item--write").count();
  await page.keyboard.press("Escape");
  return count;
}

/** Check the band and the tags at one width and theme: inside the page, and accessible. */
async function checkView(page: Page, width: number, theme: "dark" | "light"): Promise<void> {
  await page.setViewportSize({ width, height: width < 600 ? 844 : 900 });
  const current = await page.locator("html").getAttribute("data-theme");
  if ((current === "light" ? "light" : "dark") !== theme) {
    await page.getByRole("button", { name: /Switch to (?:dark|light) theme/u }).click();
  }
  const band = page.locator(".fleet-band");
  await expect(band).toBeVisible();
  const placed = await band.evaluate(element => ({
    right: element.getBoundingClientRect().right,
    pageWidth: document.documentElement.scrollWidth,
  }));
  expect(placed.right).toBeLessThanOrEqual(width + 1);
  expect(placed.pageWidth).toBeLessThanOrEqual(width);
  const result = await page.evaluate(async () =>
    (window as unknown as AxeWindow).axe.run({
      include: [["header"], [".fleet-band"], [".panel__scope"], [".fleet-board"], [".fleet-claims"]],
    }),
  );
  expect(result.violations, `${width}/${theme}: ${JSON.stringify(result.violations)}`).toEqual([]);
  const shot = await page.screenshot({ animations: "disabled" });
  await test.info().attach(`fleet-mirror-${width}-${theme}`, { body: shot, contentType: "image/png" });
}

test("the Fleet mirror is a scope for its observers alone: advisory, read only, withdrawn with its grant", async ({
  page,
  browser,
}) => {
  test.setTimeout(180_000);
  let harness: Started | undefined;
  try {
    const started = await startHarness({ fleetMirror: true });
    harness = started.harness;
    const grants = started.fleetGrants;
    if (grants === null) throw new Error("the harness named no observer list");

    // The viewer is not on the observer list: no entry, and nothing said about it.
    const viewerPage = await browser.newPage();
    await unlock(viewerPage, started.url, viewer);
    const viewerSelect = viewerPage.getByRole("banner").getByRole("combobox", { name: SCOPE_LABEL });
    await expect(viewerSelect.locator('option[value="fleet"]')).toHaveCount(0);
    await viewerPage.close();

    await page.addInitScript({ content: axe.source });
    await page.setViewportSize({ width: 1440, height: 900 });
    await unlock(page, started.url, operator);
    expect(await writeCount(page)).toBeGreaterThan(0);
    const select = page.getByRole("banner").getByRole("combobox", { name: SCOPE_LABEL });
    await expect(select.locator('option[value="fleet"]')).toHaveText("Fleet mirror (advisory)", { timeout: 20_000 });
    const asked = new Set<string>();
    let recording = false;
    page.on("request", request => {
      const path = new URL(request.url()).pathname;
      if (recording && !path.startsWith("/cockpit/")) asked.add(path);
    });
    await select.selectOption("fleet");
    await expect(page).toHaveURL(/[?&]scope=fleet(?:&|$)/u);

    const band = page.locator(".fleet-band");
    await expect(band).toContainText(BAND, { timeout: 20_000 });
    // The switch has committed: from here the cockpit reads the mirror, its grant and the login's
    // access check, and nothing else of this hub, neither a feed's first read nor one more poll. A
    // read the whole-hub scope began before the switch committed is not this scope's, so it is not
    // counted.
    recording = true;
    const recordedFrom = Date.now();
    await expect(band).toContainText("not this hub's state · writes off");
    await expect(band).not.toContainText("stale");
    await expect(page.getByRole("banner").getByLabel("Live transport: observed · polled")).toBeVisible();

    const board = region(page, "Task board");
    for (const task of ["MIRROR-1", "MIRROR-2", "MIRROR-3"]) await expect(board).toContainText(task);
    await expect(board.locator(".fleet-board__badge")).toContainText("conflict: field: status");
    await expect(board).toContainText("claim hub harbour-hub");
    await expect(board.getByText("advisory · Fleet mirror fleet-e2e", { exact: true })).toBeVisible();
    const claims = region(page, "File-scope claims");
    await expect(claims.getByRole("heading", { name: "harbour-hub" })).toBeVisible();
    await expect(claims.getByRole("heading", { name: "lighthouse-hub" })).toBeVisible();
    await expect(claims).not.toContainText("MIRROR-3");
    await expect(region(page, "Findings stream")).toContainText("Not observed in the Fleet mirror");
    await expect(region(page, "Fleet roster")).toContainText("Not observed in the Fleet mirror");
    await band.getByText(/Hub health/u).click();
    await expect(band.getByRole("row", { name: /lighthouse-hub/u })).toContainText("pages");
    expect(await writeCount(page)).toBe(0);
    await page.waitForTimeout(Math.max(11_000, WATCHED_MS - (Date.now() - recordedFrom)));
    expect([...asked].filter(path => !MIRROR_PATHS.has(path))).toEqual([]);
    expect(asked.has("/fleet-observed.json")).toBe(true);

    for (const width of [1440, 1280, 390]) {
      for (const theme of ["dark", "light"] as const) {
        await test.step(`${width} pixels, ${theme}`, () => checkView(page, width, theme));
      }
    }
    await page.setViewportSize({ width: 1440, height: 900 });

    // Fleet's export grows old: the band says it is stale.
    const exportPath = grants.replace(/fleet-observers\.json$/u, "fleet-observed.json");
    const exported = JSON.parse(readFileSync(exportPath, "utf-8")) as { exported_at: number };
    exported.exported_at = Date.now() / 1000 - 600;
    writeFileSync(exportPath, JSON.stringify(exported), { mode: 0o600 });
    await expect(band.locator(".fleet-band__stale")).toHaveText("stale", { timeout: 25_000 });
    await expect(band).toContainText("exported 10 min ago");

    // The grant is removed during the scope: the next read is refused, and the scope is withdrawn.
    writeFileSync(grants, JSON.stringify({ version: 1, observers: ["admin"] }), { mode: 0o600 });
    await expect(page.locator(".fleet-notice")).toContainText("The Fleet mirror grant ended.", { timeout: 25_000 });
    await expect(page).not.toHaveURL(/scope=fleet/u);
    await expect(page.locator(".fleet-band")).toHaveCount(0);
    await expect(select.locator('option[value="fleet"]')).toHaveCount(0, { timeout: 70_000 });
  } finally {
    await stop(harness);
  }
});
