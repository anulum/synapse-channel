// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the setup apply journey on a temporary host: preview, confirm, apply once, read back, replay

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import axe from "axe-core";

import { startHarness, stop, type Started } from "./fleetHarness";

const admin = process.env["SYNAPSE_COCKPIT_E2E_ADMIN_TOKEN"];
if (!admin) throw new Error("SYNAPSE_COCKPIT_E2E_ADMIN_TOKEN is required");

const CONFIRM = "I confirm this plan. Apply it once on this host.";
const APPLIED = "Read back: applied. The receipt chain holds this apply, and every change is in place.";
const REFUSED = "Read back: refused. The receipt chain records why; no change from this plan was kept.";

interface AxeWindow extends Window {
  readonly axe: {
    run(root: Document): Promise<{ readonly violations: readonly { readonly id: string }[] }>;
  };
}

interface ServedReceipt {
  readonly outcome: string;
  readonly reason: string;
  readonly capability: string;
}

async function chain(request: APIRequestContext, origin: string): Promise<[string, string, string][]> {
  const response = await request.get(`${origin}/setup/receipts.json`, { headers: { Authorization: `Bearer ${admin}` } });
  expect(response.status()).toBe(200);
  const document = (await response.json()) as { readonly receipts: readonly ServedReceipt[] };
  return document.receipts.map((receipt) => [receipt.capability, receipt.outcome, receipt.reason]);
}

async function expectAccessible(page: Page, label: string): Promise<void> {
  const result = await page.evaluate(async () => (window as unknown as AxeWindow).axe.run(document));
  expect(result.violations, `${label}: ${JSON.stringify(result.violations)}`).toEqual([]);
}

test("an admin applies a confirmed setup plan once, reads it back as applied, and sees its replay refused", async ({
  page,
  request,
}) => {
  test.setTimeout(150_000);
  let harness: Started | undefined;
  const consoleLines: string[] = [];
  page.on("console", (message) => consoleLines.push(message.text()));
  try {
    const started = await startHarness({ setup: true });
    harness = started.harness;
    const origin = new URL(started.url).origin;
    const served = await request.get(`${origin}/capabilities.json`, { headers: { Authorization: `Bearer ${admin}` } });
    expect((await served.json()).setup).toEqual({ advertised: true, reason: "ready" });

    await page.addInitScript({ content: axe.source });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`${started.url}?lang=en#panel=attention`);
    await page.getByLabel("Dashboard bearer token").fill(admin);
    await page.getByRole("button", { name: "unlock cockpit" }).click();
    await expect(page.getByRole("banner").getByText("live", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Open local setup assistant" }).click();
    const assistant = page.getByRole("dialog", { name: "Setup assistant" });
    await expect(assistant).toContainText("Local setup · applies only what you confirm");
    await assistant.getByRole("button", { name: "Apply", exact: true }).click();

    // Preview: what will change, and the hub it restarts, before anything is applied.
    await assistant.getByRole("button", { name: "Preview the plan" }).click();
    const plan = assistant.getByRole("region", { name: "Planned changes" });
    await expect(plan).toContainText("Profile local-single-user");
    await expect(plan.getByRole("listitem")).toHaveCount(1);
    await expect(plan.getByRole("listitem")).toContainText("Run the local loopback hub as a user service");
    await expect(plan).toContainText(/The hub \(process \d+\) is restarted\./u);
    const applyButton = assistant.getByRole("button", { name: "Apply the plan" });
    await expect(applyButton).toBeDisabled();
    expect(await chain(request, origin)).toEqual([["setup_plan", "planned", "none"]]);
    await expectAccessible(page, "preview");

    // Confirm and apply once; the cockpit says applied only after reading the receipt back.
    await assistant.getByLabel(CONFIRM).check();
    await applyButton.click();
    const answer = assistant.getByRole("region", { name: "What the dashboard did" });
    await expect(answer.getByRole("status")).toHaveText(APPLIED);
    await expect(answer).toContainText("outcome applied · reason none");
    await expect(answer).toContainText("unsatisfied → satisfied");
    await expect(assistant.getByLabel(CONFIRM)).not.toBeChecked();
    expect(await chain(request, origin)).toEqual([
      ["setup_apply", "applied", "none"],
      ["setup_apply", "authorised", "none"],
      ["setup_plan", "planned", "none"],
    ]);
    await expectAccessible(page, "applied");

    // A replay of the same confirmation is refused by the dashboard, and shown as refused.
    await assistant.getByLabel(CONFIRM).check();
    await applyButton.click();
    await expect(answer).toContainText("The dashboard refused: setup apply refused: plan_replayed");
    await expect(answer.getByRole("status")).toHaveText(REFUSED);
    await expect(answer).toContainText("outcome refused · reason plan_replayed");
    expect((await chain(request, origin))[0]).toEqual(["setup_apply", "refused", "plan_replayed"]);
    await expectAccessible(page, "replayed");

    await assistant.getByRole("button", { name: "Close setup assistant" }).click();
    await page.getByRole("button", { name: /Switch to (?:dark|light) theme/u }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "Open local setup assistant" }).click();
    const narrow = page.getByRole("dialog", { name: "Setup assistant" });
    await narrow.getByRole("button", { name: "Apply", exact: true }).click();
    await narrow.getByRole("button", { name: "Preview the plan" }).click();
    await expect(narrow.getByRole("region", { name: "Planned changes" })).toBeVisible();
    expect(await narrow.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    await expectAccessible(page, "narrow, other theme");

    await expect(page.locator("body")).not.toContainText(admin);
    expect(consoleLines.some((line) => line.includes(admin))).toBe(false);
  } finally {
    await stop(harness);
  }
});
