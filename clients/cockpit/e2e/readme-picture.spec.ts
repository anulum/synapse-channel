// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — the README cockpit picture, taken from a demonstration fleet

import { createHash } from "node:crypto";
import { writeFileSync } from "node:fs";
import { basename, resolve } from "node:path";
import { expect, test } from "@playwright/test";

import { startFleet, startHarness, stop, type Fleet, type Started } from "./fleetHarness";

const bearer = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
if (!bearer) throw new Error("SYNAPSE_COCKPIT_E2E_TOKEN is required");

const WIDTH = 1280;
const HEIGHT = 900;
test("the README picture shows a working fleet in the built cockpit", async ({ page }) => {
  test.setTimeout(90_000);
  let harness: Started | undefined;
  let fleet: Fleet | undefined;
  try {
    const started = await startHarness();
    harness = started.harness;
    const url = started.url;

    // The fleet's first act leaves the standing state a cockpit finds: the plan
    // and two of its claims. The operator opens the cockpit on it; the second
    // act then happens before the open page, which is what the activity spine
    // draws.
    fleet = await startFleet(["--two-acts"]);
    await page.emulateMedia({ colorScheme: "dark" });
    await page.setViewportSize({ width: WIDTH, height: HEIGHT });
    await page.goto(url);
    await page.getByLabel("Dashboard bearer token").fill(bearer);
    await page.getByRole("button", { name: "unlock cockpit" }).click();
    const banner = page.getByRole("banner");
    await expect(banner.getByText("live", { exact: true })).toBeVisible();
    await expect(banner.getByText("stream", { exact: true })).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await expect(page.locator("html")).not.toHaveAttribute("data-theme");
    await expect(page.locator("html")).not.toHaveAttribute("data-density");
    await expect(banner.getByRole("combobox", { name: "Interface language" })).toHaveValue("en");
    for (const claim of fleet.claims) {
      await expect(page.getByRole("region", { name: "File-scope claims", exact: true })).toContainText(claim);
    }
    const { participants, tasks, claims } = await fleet.secondAct();
    expect(claims.length).toBe(fleet.claims.length + 1);

    const region = (name: string) => page.getByRole("region", { name, exact: true });
    for (const name of ["Fleet roster", "File-scope claims", "Task board", "Risk rail", "Findings stream", "Signal log"]) {
      await expect(region(name)).toBeVisible();
    }
    for (const participant of participants) {
      await expect(region("Fleet roster").getByRole("button", { name: `Inspect agent ${participant}`, exact: true }))
        .toBeVisible();
    }
    for (const task of tasks) await expect(region("Task board")).toContainText(task);
    for (const claim of claims) {
      await expect(region("File-scope claims")).toContainText(claim);
      await expect(region("Signal log")).toContainText(`claimed ${claim}`);
    }
    await expect(region("Risk rail")).toContainText("amber", { ignoreCase: true });
    await expect(region("Risk rail")).toContainText("HARBOUR-103");
    // The fleet's plan holds four unfinished prerequisites, all posted in its
    // first act, so the cockpit's first reading of the hub's report finds
    // them. Each prerequisite is one row, worded as the hub reports it: the
    // task is in the log and unfinished, not absent.
    const rail = region("Risk rail");
    await expect(rail).toContainText("hub health anomalies · 4", { timeout: 20_000 });
    await expect(rail.getByText("waits on unfinished HARBOUR-101", { exact: true })).toHaveCount(3);
    await expect(rail.getByText("waits on unfinished HARBOUR-104", { exact: true })).toHaveCount(1);
    await expect(rail).not.toContainText("absent");
    await expect(region("Reliability evidence")).not.toContainText("Waiting for the hub.");
    // Arrival notices are transient and cover the panel beneath them; the picture is the settled view.
    await expect(page.locator(".toasts")).toHaveCount(0, { timeout: 30_000 });
    await expect(banner.getByText("stream", { exact: true })).toBeVisible();

    expect(await page.evaluate(() => {
      const targets = document.querySelectorAll("header, header button, header select, section[aria-label]");
      return Array.from(targets).flatMap(element => {
        const rect = element.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return [];
        return rect.left < -1 || rect.right > innerWidth + 1
          ? [{ label: element.getAttribute("aria-label") ?? element.textContent, right: rect.right }]
          : [];
      });
    })).toEqual([]);

    const picture = await page.screenshot({ animations: "disabled" });
    expect(picture.readUInt32BE(16)).toBe(WIDTH);
    expect(picture.readUInt32BE(20)).toBe(HEIGHT);
    await test.info().attach("readme-cockpit", { body: picture, contentType: "image/png" });

    const destination = process.env["SYNAPSE_COCKPIT_README_PICTURE"];
    if (destination) {
      const target = resolve(destination);
      writeFileSync(target, picture);
      writeFileSync(target.replace(/\.png$/u, ".json"), `${JSON.stringify({
        picture: basename(target),
        sha256: createHash("sha256").update(picture).digest("hex"),
        width: WIDTH,
        height: HEIGHT,
        colour_scheme: "dark",
        language: "EN",
        journey: "clients/cockpit/e2e/readme-picture.spec.ts",
        fleet: "clients/cockpit/e2e/demo_fleet.py",
        participants,
        tasks,
        claims,
      }, null, 2)}\n`);
    }
  } finally {
    await stop(fleet?.started);
    await stop(harness);
  }
});
