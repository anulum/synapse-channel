// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — the cockpit's scope: chosen in the header, kept in the address, stated by each panel

import { expect, test, type Page } from "@playwright/test";
import axe from "axe-core";
import { rosterInspectName } from "../src/lib/roster";

import { startFleet, startHarness, stop, type Fleet, type Started } from "./fleetHarness";

const bearer = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
if (!bearer) throw new Error("SYNAPSE_COCKPIT_E2E_TOKEN is required");

const SCOPE_LABEL = "Scope: one project's part of the fleet";

interface Namespace {
  readonly namespace: string;
  readonly tasks: number;
  readonly agents: number;
}

interface AxeWindow extends Window {
  readonly axe: {
    run(context: { readonly include: readonly (readonly string[])[] }): Promise<{
      readonly violations: readonly { readonly id: string }[];
    }>;
  };
}

interface Project {
  /** Every task of the project, closed ones included: the hub counts them all. */
  readonly tasks: readonly string[];
  /** The tasks the board draws. */
  readonly open: readonly string[];
  readonly agents: readonly string[];
}

/** The tasks and participants of one of the fleet's two projects. */
function project(fleet: Fleet, prefix: string, namespace: string): Project {
  const tasks = fleet.tasks.filter(task => task.startsWith(prefix));
  return {
    tasks,
    open: tasks.filter(task => !fleet.closed.includes(task)),
    agents: fleet.participants.filter(name => name.startsWith(`${namespace}/`)),
  };
}

/** The namespaces the whole hub's snapshot offers as scopes. */
async function namespaces(page: Page, origin: string): Promise<readonly Namespace[]> {
  const response = await page.request.get(`${origin}/snapshot.json`, {
    headers: { Authorization: `Bearer ${bearer}` },
  });
  expect(response.status()).toBe(200);
  const document = (await response.json()) as { scope: { namespaces: Namespace[] } };
  return document.scope.namespaces;
}

/** See the board and the roster hold one project's work and none of the other's. */
async function expectNarrowed(page: Page, kept: Project, left: Project): Promise<void> {
  const region = (name: string) => page.getByRole("region", { name, exact: true });
  for (const task of kept.open) await expect(region("Task board")).toContainText(task);
  for (const task of left.open) await expect(region("Task board")).not.toContainText(task);
  for (const agent of kept.agents) {
    await expect(region("Fleet roster").getByRole("button", { name: rosterInspectName(agent), exact: true }))
      .toBeVisible();
  }
  for (const agent of left.agents) {
    await expect(region("Fleet roster").getByRole("button", { name: rosterInspectName(agent), exact: true }))
      .toHaveCount(0);
  }
  await expect(region("Task board").getByText("scope: project:harbour", { exact: true })).toBeVisible();
  await expect(region("Reliability evidence").getByText("scope: whole hub", { exact: true })).toBeVisible();
}

/** Switch the cockpit to one theme and wait until its colours have settled. */
async function useTheme(page: Page, theme: "dark" | "light"): Promise<void> {
  const current = await page.locator("html").getAttribute("data-theme");
  if ((current === "light" ? "light" : "dark") !== theme) {
    await page.getByRole("button", { name: /Switch to (?:dark|light) theme/u }).click();
  }
  await expect
    .poll(() =>
      page.locator(".roster-row").first().evaluate(row => {
        const probe = document.createElement("span");
        probe.style.color = "var(--panel-2)";
        document.body.append(probe);
        const expected = getComputedStyle(probe).color;
        probe.remove();
        return getComputedStyle(row).backgroundColor === expected;
      }),
    )
    .toBe(true);
}

/** Check the active scope's control and tags at one width and theme. */
async function checkView(page: Page, width: number, theme: "dark" | "light"): Promise<void> {
  await page.setViewportSize({ width, height: width < 600 ? 844 : 900 });
  await useTheme(page, theme);
  const select = page.getByRole("banner").getByRole("combobox", { name: SCOPE_LABEL });
  await select.scrollIntoViewIfNeeded();
  await expect(select).toBeVisible();
  await expect(select).toHaveValue("project:harbour");
  const placed = await select.evaluate(element => {
    const box = element.getBoundingClientRect();
    const header = document.querySelector("header")!.getBoundingClientRect();
    return {
      inside: box.top >= header.top - 1 && box.bottom <= header.bottom + 1,
      pageWidth: document.documentElement.scrollWidth,
    };
  });
  expect(placed.inside).toBe(true);
  expect(placed.pageWidth).toBeLessThanOrEqual(width);
  // The audit covers what the scope adds: the header with its selector and every
  // panel's scope tag. It also covers the roster, whose namespaced rows the
  // accessibility journey's bare-named harness never draws. The whole deck's
  // audit is the accessibility journey's.
  const result = await page.evaluate(async () =>
    (window as unknown as AxeWindow).axe.run({ include: [["header"], [".panel__scope"], [".roster"]] }),
  );
  expect(result.violations, `${width}/${theme}: ${JSON.stringify(result.violations)}`).toEqual([]);
  const shot = await page.screenshot({ animations: "disabled" });
  await test.info().attach(`scope-${width}-${theme}`, { body: shot, contentType: "image/png" });
}

/** Choose a scope in the header and see the address, the data and the panels follow it. */
async function chooseHarbour(page: Page, fleet: Fleet, origin: string): Promise<void> {
  const harbour = project(fleet, "HARBOUR", "harbour");
  const lighthouse = project(fleet, "LIGHT", "lighthouse");
  await expect
    .poll(async () => (await namespaces(page, origin)).find(entry => entry.namespace === "harbour"), {
      timeout: 30_000,
    })
    .toEqual({ namespace: "harbour", tasks: harbour.tasks.length, agents: harbour.agents.length });
  const select = page.getByRole("banner").getByRole("combobox", { name: SCOPE_LABEL });
  await expect(select.locator("option")).toContainText([
    /^whole hub · \d+ tasks · \d+ agents$/u,
    /^no project · \d+ tasks · \d+ agents$/u,
    `harbour · ${harbour.tasks.length} tasks · ${harbour.agents.length} agents`,
    `lighthouse · ${lighthouse.tasks.length} tasks · ${lighthouse.agents.length} agents`,
  ]);
  await select.selectOption("project:harbour");
  await expect(page).toHaveURL(/[?&]scope=project%3Aharbour(?:&|$)/u);
  await expectNarrowed(page, harbour, lighthouse);

  await page.reload();
  await expect(page.getByRole("banner").getByText("live", { exact: true })).toBeVisible();
  await expect(select).toHaveValue("project:harbour");
  await expectNarrowed(page, harbour, lighthouse);
}

/** Back returns to the whole hub with no panel tagged; Forward restores the scope. */
async function expectBackAndForward(page: Page): Promise<void> {
  const select = page.getByRole("banner").getByRole("combobox", { name: SCOPE_LABEL });
  const board = page.getByRole("region", { name: "Task board", exact: true });
  await page.goBack();
  await expect(select).toHaveValue("all");
  await expect(board).toContainText("LIGHT-201");
  await expect(page.locator(".panel__scope")).toHaveCount(0);
  await page.goForward();
  await expect(select).toHaveValue("project:harbour");
  await expect(board.getByText("scope: project:harbour", { exact: true })).toBeVisible();
  await expect(board).not.toContainText("LIGHT-201");
}

/** Time travel rebuilds the whole hub's past, and the panels that show it say so. */
async function expectReplayShowsWholeHub(page: Page): Promise<void> {
  const board = page.getByRole("region", { name: "Task board", exact: true });
  const modes = page.getByRole("group", { name: "Fleet evidence time mode" });
  await modes.getByRole("button", { name: "history", exact: true }).click();
  await expect(board.getByText("scope: whole hub", { exact: true })).toBeVisible({ timeout: 20_000 });
  const workbench = page.getByRole("region", { name: "Fleet replay workbench", exact: true });
  await expect(workbench.getByText("scope: whole hub", { exact: true })).toBeVisible();
  await modes.getByRole("button", { name: "live", exact: true }).click();
  await expect(board.getByText("scope: project:harbour", { exact: true })).toBeVisible();
  // The workspace wrote the address twice; it keeps the scope's key.
  await expect(page).toHaveURL(/[?&]scope=project%3Aharbour(?:&|$)/u);
}

test("a scope chosen in the header narrows the cockpit, survives a reload and is named by each panel", async ({
  page,
}) => {
  test.setTimeout(150_000);
  let harness: Started | undefined;
  let fleet: Fleet | undefined;
  try {
    const started = await startHarness();
    harness = started.harness;
    fleet = await startFleet();
    await page.addInitScript({ content: axe.source });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(started.url);
    await page.getByLabel("Dashboard bearer token").fill(bearer);
    await page.getByRole("button", { name: "unlock cockpit" }).click();
    await expect(page.getByRole("banner").getByText("live", { exact: true })).toBeVisible();
    await chooseHarbour(page, fleet, new URL(started.url).origin);
    for (const width of [1440, 1280, 390]) {
      for (const theme of ["dark", "light"] as const) {
        await test.step(`${width} pixels, ${theme}`, () => checkView(page, width, theme));
      }
    }

    await page.setViewportSize({ width: 1440, height: 900 });
    await expectBackAndForward(page);
    await expectReplayShowsWholeHub(page);
  } finally {
    await stop(fleet?.started);
    await stop(harness);
  }
});
