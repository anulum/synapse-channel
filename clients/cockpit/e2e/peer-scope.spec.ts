// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — an observed peer hub as a scope: advisory on every panel, read only, never mixed

import { expect, test, type Page } from "@playwright/test";
import axe from "axe-core";

import { startFleet, startHarness, stop, type Fleet, type Started } from "./fleetHarness";

const bearer = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
if (!bearer) throw new Error("SYNAPSE_COCKPIT_E2E_TOKEN is required");

const PEER = "east-harbour";
const SCOPE_LABEL = "Scope: one project's part of the fleet";

interface AxeWindow extends Window {
  readonly axe: {
    run(context: { readonly include: readonly (readonly string[])[] }): Promise<{
      readonly violations: readonly { readonly id: string }[];
    }>;
  };
}

const region = (page: Page, name: string) => page.getByRole("region", { name, exact: true });

/** The panels of the deck, and whether each one names the scope it shows. */
async function panelTags(page: Page): Promise<{ readonly label: string; readonly tag: string }[]> {
  return page.locator("[role=main] section.panel").evaluateAll(sections =>
    sections.map(section => ({
      label: section.getAttribute("aria-label") ?? "",
      tag: section.querySelector(".panel__scope")?.textContent ?? "",
    })),
  );
}

/** Choose the observed hub and see the deck show it alone, advisory, read only. */
async function enterPeer(page: Page, origin: string): Promise<void> {
  await expect
    .poll(async () => {
      const response = await page.request.get(`${origin}/snapshot.json`, {
        headers: { Authorization: `Bearer ${bearer}` },
      });
      return ((await response.json()) as { scope: { peers: string[] } }).scope.peers;
    }, { timeout: 30_000 })
    .toEqual([PEER]);
  const select = page.getByRole("banner").getByRole("combobox", { name: SCOPE_LABEL });
  await expect(select.locator("optgroup")).toHaveAttribute("label", "Observed hubs (advisory)");
  await select.selectOption(`peer:${PEER}`);
  await expect(page).toHaveURL(/[?&]scope=peer%3Aeast-harbour(?:&|$)/u);
  const band = page.locator(".peer-band");
  await expect(band).toContainText(`Advisory: observed from ${PEER}.`);
  await expect(band).toContainText(/reached · cursor \d+ · lag 0/u, { timeout: 20_000 });
}

/** The peer's plan and claim are shown, the local fleet's are not, and every panel says which. */
async function expectPeerDeck(page: Page, fleet: Fleet): Promise<void> {
  for (const task of ["EAST-1", "EAST-2"]) await expect(region(page, "Task board")).toContainText(task);
  for (const task of fleet.tasks) await expect(region(page, "Task board")).not.toContainText(task);
  await expect(region(page, "File-scope claims")).toContainText("EAST-1");
  // The peer's log names its claim owner; nothing here shows whether it listens.
  await expect(region(page, "Fleet roster").getByText("observed", { exact: true })).toBeVisible();
  await expect(region(page, "Fleet roster")).not.toContainText("active, not listening");
  for (const name of ["Task board", "File-scope claims", "Fleet roster", "Risk rail"]) {
    await expect(region(page, name).getByText(`advisory · observed from ${PEER}`, { exact: true })).toBeVisible();
  }
  await expect(region(page, "Findings stream")).toContainText(`Not observed for ${PEER}`);
  const tags = await panelTags(page);
  expect(tags.length).toBeGreaterThan(0);
  expect(tags.filter(panel => panel.tag === "")).toEqual([]);
}

/** The palette offers no write in a peer scope, and offers it again on leaving the scope. */
async function expectWrites(page: Page, present: boolean): Promise<void> {
  await page.getByRole("button", { name: "commands Ctrl K: Open command palette" }).click();
  await expect(page.locator(".palette__item--write")).toHaveCount(present ? 3 : 0);
  await page.keyboard.press("Escape");
}

/** Check the band and the tags at one width and theme: inside the page, and accessible. */
async function checkView(page: Page, width: number, theme: "dark" | "light"): Promise<void> {
  await page.setViewportSize({ width, height: width < 600 ? 844 : 900 });
  const current = await page.locator("html").getAttribute("data-theme");
  if ((current === "light" ? "light" : "dark") !== theme) {
    await page.getByRole("button", { name: /Switch to (?:dark|light) theme/u }).click();
  }
  const band = page.locator(".peer-band");
  await expect(band).toBeVisible();
  const placed = await band.evaluate(element => ({
    right: element.getBoundingClientRect().right,
    pageWidth: document.documentElement.scrollWidth,
  }));
  expect(placed.right).toBeLessThanOrEqual(width + 1);
  expect(placed.pageWidth).toBeLessThanOrEqual(width);
  const result = await page.evaluate(async () =>
    (window as unknown as AxeWindow).axe.run({ include: [["header"], [".peer-band"], [".panel__scope"]] }),
  );
  expect(result.violations, `${width}/${theme}: ${JSON.stringify(result.violations)}`).toEqual([]);
  const shot = await page.screenshot({ animations: "disabled" });
  await test.info().attach(`peer-scope-${width}-${theme}`, { body: shot, contentType: "image/png" });
}

test("an observed hub is a scope of its own: advisory on every panel, read only, and never mixed", async ({
  page,
}) => {
  test.setTimeout(150_000);
  let harness: Started | undefined;
  let fleet: Fleet | undefined;
  try {
    const started = await startHarness({ observedPeer: true });
    harness = started.harness;
    fleet = await startFleet();
    await page.addInitScript({ content: axe.source });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(started.url);
    await page.getByLabel("Dashboard bearer token").fill(bearer);
    await page.getByRole("button", { name: "unlock cockpit" }).click();
    await expect(page.getByRole("banner").getByText("live", { exact: true })).toBeVisible();
    await expectWrites(page, true);

    await enterPeer(page, new URL(started.url).origin);
    await expectPeerDeck(page, fleet);
    await expectWrites(page, false);
    for (const width of [1440, 1280, 390]) {
      for (const theme of ["dark", "light"] as const) {
        await test.step(`${width} pixels, ${theme}`, () => checkView(page, width, theme));
      }
    }

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.getByRole("banner").getByRole("combobox", { name: SCOPE_LABEL }).selectOption("all");
    await expect(page.locator(".peer-band")).toHaveCount(0);
    await expect(region(page, "Task board")).toContainText(fleet.tasks[0]!);
    await expect(page.locator(".panel__scope")).toHaveCount(0);
    await expectWrites(page, true);
  } finally {
    await stop(fleet?.started);
    await stop(harness);
  }
});
