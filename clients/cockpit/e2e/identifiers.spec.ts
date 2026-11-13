// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — identifiers keep their distinguishing end; a phone shows state first

import { writeFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

import { startFleet, startHarness, stop, type Fleet, type Started } from "./fleetHarness";

const bearer = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
if (!bearer) throw new Error("SYNAPSE_COCKPIT_E2E_TOKEN is required");

interface CutIdentifier {
  readonly where: string;
  readonly value: string;
  readonly reason: string;
}

/**
 * Every identifier drawn in the deck whose whole value is not its title or
 * whose distinguishing end is not fully inside its box and its panel.
 */
async function identifiers(page: Page): Promise<{ checked: number; cut: CutIdentifier[] }> {
  return page.evaluate(() => {
    const cut: { where: string; value: string; reason: string }[] = [];
    let checked = 0;
    for (const element of document.querySelectorAll<HTMLElement>('[role="main"] .ident')) {
      const box = element.getBoundingClientRect();
      if (box.width === 0 || box.height === 0) continue;
      checked += 1;
      const value = element.textContent ?? "";
      const where = element.closest("section[aria-label]")?.getAttribute("aria-label") ?? "deck";
      if (element.title !== value) cut.push({ where, value, reason: "the title is not the whole value" });
      let limit = box.right;
      for (let node = element.parentElement; node !== null && node.getAttribute("role") !== "main"; node = node.parentElement) {
        if (getComputedStyle(node).overflowX !== "visible") {
          limit = Math.min(limit, node.getBoundingClientRect().right);
        }
      }
      const head = element.querySelector<HTMLElement>(".ident__head")!;
      const tail = element.querySelector<HTMLElement>(".ident__tail");
      if (tail === null) {
        if (head.scrollWidth > head.clientWidth + 1) cut.push({ where, value, reason: "a short value is cut" });
        continue;
      }
      const end = tail.getBoundingClientRect();
      if (end.width === 0 || end.right > limit + 0.5) cut.push({ where, value, reason: "the end is cut" });
    }
    return { checked, cut };
  });
}

/** How far, in pixels, the signal log's table reaches beyond its panel. */
async function logOverhang(page: Page): Promise<number> {
  return page.evaluate(() => {
    const table = document.querySelector<HTMLElement>("table.log")!;
    const panel = table.closest("section")!;
    return Math.round(table.getBoundingClientRect().right - panel.getBoundingClientRect().right);
  });
}

/** The end the roster shows for one participant, and whether the row cut the name at all. */
async function rosterEnd(page: Page, participant: string): Promise<string> {
  const name = page
    .getByRole("region", { name: "Fleet roster", exact: true })
    .getByRole("button", { name: `Inspect agent ${participant}`, exact: true })
    .locator(".ident");
  await expect(name).toBeVisible();
  return (await name.locator(".ident__tail").textContent()) ?? "";
}

test("identifiers keep their ends at three widths and a phone shows state first", async ({ page }) => {
  test.setTimeout(150_000);
  let harness: Started | undefined;
  let fleet: Fleet | undefined;
  try {
    const started = await startHarness();
    harness = started.harness;
    await page.emulateMedia({ colorScheme: "dark" });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(started.url);
    await page.getByLabel("Dashboard bearer token").fill(bearer);
    await page.getByRole("button", { name: "unlock cockpit" }).click();
    const banner = page.getByRole("banner");
    await expect(banner.getByText("live", { exact: true })).toBeVisible();
    await page.evaluate(() => document.fonts.ready);

    // A fleet whose names differ only at their ends, as a large installation's do.
    fleet = await startFleet(["--long-names"]);
    const { participants, tasks, closed } = fleet;
    // The snapshot carries open work in full; closed tasks are counted, not drawn.
    const open = tasks.filter(task => !closed.includes(task));
    const api = participants.find(name => name.endsWith("-api"))!;
    const web = participants.find(name => name.endsWith("-web"))!;
    expect(api.slice(0, -4)).toBe(web.slice(0, -4));
    const region = (name: string) => page.getByRole("region", { name, exact: true });
    for (const participant of participants) {
      await expect(region("Fleet roster").getByRole("button", { name: `Inspect agent ${participant}`, exact: true }))
        .toBeVisible();
    }
    for (const task of open) await expect(region("Task board")).toContainText(task);
    await expect(region("Task board").getByText(`${closed.length} closed not loaded`, { exact: true }))
      .toBeVisible();
    await expect(page.locator(".toasts")).toHaveCount(0, { timeout: 30_000 });

    const picture = async (name: string): Promise<void> => {
      const shot = await page.screenshot({ animations: "disabled" });
      await test.info().attach(name, { body: shot, contentType: "image/png" });
      const folder = process.env["SYNAPSE_COCKPIT_IDENTIFIER_PICTURES"];
      if (folder) writeFileSync(`${folder}/${name}.png`, shot);
    };

    for (const width of [1440, 1280]) {
      await test.step(`${width} pixels`, async () => {
        await page.setViewportSize({ width, height: 900 });
        const seen = await identifiers(page);
        expect(seen.cut).toEqual([]);
        expect(seen.checked).toBeGreaterThanOrEqual(participants.length + open.length);
        expect(await logOverhang(page)).toBeLessThanOrEqual(1);
        expect(await rosterEnd(page, api)).toBe("-api");
        expect(await rosterEnd(page, web)).toBe("-web");
        await picture(`identifiers-${width}`);
      });
    }

    await test.step("390 pixels", async () => {
      await page.setViewportSize({ width: 390, height: 844 });
      const sections = page.getByRole("navigation", { name: "Deck section", exact: true });
      await expect(sections).toBeVisible();

      const layout = await page.evaluate(() => {
        const header = document.querySelector<HTMLElement>("header.hud")!.getBoundingClientRect();
        const controls = document.querySelector<HTMLElement>(".hud__controls")!;
        const centres = Array.from(controls.children, child => {
          const box = child.getBoundingClientRect();
          return Math.round(box.top + box.height / 2);
        });
        const state = Array.from(document.querySelectorAll<HTMLElement>('[role="main"] section[aria-label]'))
          .map(section => section.getBoundingClientRect())
          .filter(box => box.width > 0 && box.height > 0)
          .map(box => Math.round(box.top));
        const tabs = Array.from(document.querySelectorAll<HTMLElement>("[role=tab]"));
        return {
          headerHeight: Math.round(header.height),
          controlRows: new Set(centres).size,
          controlCentreSpread: Math.max(...centres) - Math.min(...centres),
          controlsScroll: controls.scrollWidth > controls.clientWidth,
          firstStateTop: Math.min(...state),
          cutTabs: tabs.filter(tab => tab.scrollWidth > tab.clientWidth + 1).map(tab => tab.textContent),
          overlappingTabs: tabs.filter((tab, index) => {
            const next = tabs[index + 1];
            return next !== undefined && tab.getBoundingClientRect().right > next.getBoundingClientRect().left + 1;
          }).map(tab => tab.textContent),
          pageWidth: document.documentElement.scrollWidth,
        };
      });
      // The controls are one row, the header takes at most a quarter of the
      // screen, and a state panel starts in the first screen.
      expect(layout.controlCentreSpread).toBeLessThanOrEqual(2);
      expect(layout.headerHeight).toBeLessThanOrEqual(844 / 4);
      expect(layout.firstStateTop).toBeLessThan(844);
      await test.info().attach("phone-layout", { body: JSON.stringify(layout), contentType: "application/json" });
      expect(layout.cutTabs).toEqual([]);
      expect(layout.overlappingTabs).toEqual([]);
      expect(layout.pageWidth).toBeLessThanOrEqual(390);

      expect(await logOverhang(page)).toBeLessThanOrEqual(1);
      for (const section of ["signals", "claims", "board", "roster"]) {
        await sections.getByRole("button", { name: section, exact: true }).click();
        const seen = await identifiers(page);
        expect(seen.cut, `${section} at 390 pixels`).toEqual([]);
        expect(seen.checked, `${section} at 390 pixels`).toBeGreaterThan(0);
        await picture(`identifiers-390-${section}`);
      }
      expect(await rosterEnd(page, api)).toBe("-api");
      expect(await rosterEnd(page, web)).toBe("-web");
    });
    await test.step("keyboard focus shows the whole value; the drawer's title copies it", async () => {
      await page.setViewportSize({ width: 1280, height: 900 });
      const inspect = region("Fleet roster").getByRole("button", { name: `Inspect agent ${api}`, exact: true });
      const head = inspect.locator(".ident__head");
      expect(await head.evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true);
      await page.keyboard.press("Tab");
      await inspect.focus();
      await expect.poll(() => head.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
      await page.keyboard.press("Enter");
      const title = page.getByRole("dialog").locator(".drawer__title.ident");
      await expect(title).toHaveText(api);
      await expect(title).toHaveAttribute("tabindex", "0");
      const copied = await title.evaluate(element => {
        const event = new ClipboardEvent("copy", {
          bubbles: true,
          cancelable: true,
          clipboardData: new DataTransfer(),
        });
        element.dispatchEvent(event);
        return { text: event.clipboardData?.getData("text/plain"), handled: event.defaultPrevented };
      });
      expect(copied).toEqual({ text: api, handled: true });
      await page.getByRole("button", { name: "Close the drawer" }).click();
      await expect(page.getByRole("dialog")).toHaveCount(0);
    });

  } finally {
    await stop(fleet?.started);
    await stop(harness);
  }
});
