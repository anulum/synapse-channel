// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — built-cockpit durable receipt and operator-audit acceptance

import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

const bearer = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
if (bearer === undefined || bearer === "") {
  throw new Error("SYNAPSE_COCKPIT_E2E_TOKEN is required by the browser gate");
}

test("the built cockpit renders store-backed receipts and operator audit", async ({ page }) => {
  await page.goto("/cockpit/");
  const liveResponse = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/live.ndjson" && response.status() === 200,
  );
  await page.getByLabel("Dashboard bearer token").fill(bearer);
  await page.getByRole("button", { name: "unlock cockpit" }).click();
  await expect(page.getByRole("banner").getByText("live", { exact: true })).toBeVisible();
  const liveLoaded = await liveResponse;
  expect(liveLoaded.request().headers()["authorization"]).toBe(`Bearer ${bearer}`);
  await expect(page.getByLabel("Live transport: stream")).toBeVisible();

  const target = `cockpit-e2e-audit-${Date.now().toString(36)}`;
  await page.keyboard.press("Control+k");
  await page.getByRole("option", { name: "operator: send a message…" }).click();
  await page.getByLabel("Message recipient").fill(target);
  await page.getByLabel("Message text").fill("durable audit browser acceptance");
  await page.getByRole("button", { name: "send" }).click();
  await expect(page.getByText(/relayed, not delivered/u)).toBeVisible();
  await page.keyboard.press("Escape");

  await page.getByRole("tab", { name: "audit" }).click();
  const receiptPanel = page.getByLabel("Universal receipts");
  await expect(receiptPanel).toContainText(target, { timeout: 8_000 });
  await expect(receiptPanel).toContainText("undelivered");
  await expect(receiptPanel).toContainText("operator:cockpit-e2e");

  const actionPanel = page.getByLabel("Governed operator actions");
  await expect(actionPanel).toContainText("cockpit-e2e-audit-seed");
  await expect(actionPanel).toContainText("release");
  await expect(actionPanel).toContainText("applied");
  await expect(actionPanel).toContainText("operator:cockpit-e2e-seed");
  await expect(page.getByLabel("Receipt and operator audit")).toContainText("durable store");
});

test("an exact audit event becomes a persistent, bounded incident export", async ({ page }) => {
  await page.goto("/cockpit/");
  await page.getByLabel("Dashboard bearer token").fill(bearer);
  await page.getByRole("button", { name: "unlock cockpit" }).click();
  await expect(page.getByRole("banner").getByText("live", { exact: true })).toBeVisible();

  await page.getByRole("tab", { name: "audit" }).click();
  const action = page
    .getByLabel("Governed operator actions")
    .getByRole("button")
    .filter({ hasText: "cockpit-e2e-audit-seed" });
  await expect(action).toBeVisible();
  await action.click();
  const drawer = page.getByRole("dialog", { name: /event #/u });
  const heading = await drawer.getByRole("heading", { name: /event #/u }).textContent();
  const sequence = Number(heading?.replace("event #", ""));
  expect(Number.isSafeInteger(sequence)).toBe(true);
  await drawer.getByRole("button", { name: /open exact event/u }).click();
  await expect(page.getByRole("tab", { name: /signal log/u })).toHaveAttribute("aria-selected", "true");

  await page.getByRole("tab", { name: "incident" }).click();
  await page.getByLabel("Incident title").fill("Seeded operator action review");
  await page.getByLabel("Working hypothesis").fill("Provisional: verify the recorded release boundary.");
  await page.getByRole("button", { name: /continue to evidence/u }).click();
  await page.getByRole("button", { name: "add current selection" }).click();
  await expect(page.getByText("1 explicit reference")).toBeVisible();
  await expect(
    page.locator(".incident-cart").getByText(`sequence ${sequence}`, { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: /continue to notes and export/u }).click();
  await page.getByLabel("Operator notes").fill("The cart contains only the selected durable sequence.");

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "export incident JSON" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^synapse-incident-.+\.json$/u);
  const downloadPath = await download.path();
  expect(downloadPath).not.toBeNull();
  const document_ = JSON.parse(await readFile(downloadPath as string, "utf8")) as {
    readonly provenance: string;
    readonly authority: string;
    readonly evidence_boundary: { readonly association: string };
    readonly incident: {
      readonly title: string;
      readonly evidence: readonly { readonly selection: { readonly seq?: number } }[];
    };
  };
  expect(document_.provenance).toBe("local-operator-draft");
  expect(document_.authority).toBe("not-a-hub-receipt-or-signed-audit-bundle");
  expect(document_.evidence_boundary.association).toBe("explicit-operator-selection-only");
  expect(document_.incident.title).toBe("Seeded operator action review");
  expect(document_.incident.evidence).toHaveLength(1);
  expect(document_.incident.evidence[0]?.selection.seq).toBe(sequence);

  await page.reload();
  await expect(page.getByRole("heading", { name: "Seeded operator action review" })).toBeVisible();
  await expect(page.getByText("1 explicit reference", { exact: true })).toBeVisible();
});
