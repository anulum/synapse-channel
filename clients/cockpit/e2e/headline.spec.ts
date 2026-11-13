// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — one counter vocabulary across the cockpit, Studio and the snapshot

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { startFleet, startHarness, stop, type Fleet, type Started } from "./fleetHarness";

const bearer = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
if (!bearer) throw new Error("SYNAPSE_COCKPIT_E2E_TOKEN is required");

type Headline = Record<string, number>;

async function headlineAt(request: APIRequestContext, url: string): Promise<Headline> {
  const response = await request.get(url, { headers: { Authorization: `Bearer ${bearer}` } });
  expect(response.status()).toBe(200);
  const document = (await response.json()) as { headline: Headline };
  return document.headline;
}

async function kpi(page: Page, label: string): Promise<number> {
  const value = page.locator(".kpi", { hasText: label }).locator(".kpi__value");
  return Number(await value.textContent());
}

test("the HUD, the board, Studio and the snapshot show one number per counter", async ({ page }) => {
  test.setTimeout(120_000);
  let harness: Started | undefined;
  let fleet: Fleet | undefined;
  try {
    const started = await startHarness();
    harness = started.harness;
    const origin = new URL(started.url).origin;
    fleet = await startFleet();
    // The fleet holds its three claims once both acts have played.
    await expect
      .poll(async () => (await headlineAt(page.request, `${origin}/snapshot.json`))["claims_active"], {
        timeout: 30_000,
      })
      .toBe(fleet.claims.length);
    const snapshot = await headlineAt(page.request, `${origin}/snapshot.json`);
    const studio = await headlineAt(page.request, `${origin}/studio.json`);
    expect(studio).toEqual(snapshot);
    expect(snapshot["tasks_blocked"]).toBe(snapshot["tasks_blocked_marked"]! + snapshot["tasks_blocked_waiting"]!);

    await page.goto(started.url);
    await page.getByLabel("Dashboard bearer token").fill(bearer);
    await page.getByRole("button", { name: "unlock cockpit" }).click();
    await expect(page.getByRole("banner").getByText("live", { exact: true })).toBeVisible();
    await expect.poll(() => kpi(page, "agents online"), { timeout: 20_000 }).toBe(snapshot["agents_live"]);
    expect(await kpi(page, "claims held")).toBe(snapshot["claims_active"]);
    expect(await kpi(page, "red risk signals")).toBe(snapshot["risk_signals_red"]);
    const board = page.getByRole("region", { name: "Task board", exact: true });
    await expect(board.getByText(`${snapshot["tasks_blocked"]} blocked`, { exact: true })).toBeVisible();

    await page.setExtraHTTPHeaders({ Authorization: `Bearer ${bearer}` });
    await page.goto(`${origin}/studio/command`);
    await expect(page.locator("#cc-agents")).toHaveText(String(snapshot["agents_live"]), { timeout: 20_000 });
    await expect(page.locator("#cc-claims")).toHaveText(`${snapshot["claims_active"]} / ${snapshot["claims_stale"]}`);
    await expect(page.locator("#cc-tasks")).toHaveText(`${snapshot["tasks_ready"]} / ${snapshot["tasks_blocked"]}`);
    await expect(page.locator("#cc-signals")).toHaveText(
      `${snapshot["risk_signals"]} / ${snapshot["risk_signals_red"]}`,
    );
    await expect(page.locator("#cc-conflicts")).toHaveText(String(snapshot["branch_conflicts"]));
  } finally {
    await stop(fleet?.started);
    await stop(harness);
  }
});
