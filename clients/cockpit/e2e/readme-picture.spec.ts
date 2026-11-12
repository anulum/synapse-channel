// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — the README cockpit picture, taken from a demonstration fleet

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, writeFileSync } from "node:fs";
import { basename, resolve } from "node:path";
import { expect, test } from "@playwright/test";

const bearer = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"];
if (!bearer) throw new Error("SYNAPSE_COCKPIT_E2E_TOKEN is required");

const WIDTH = 1280;
const HEIGHT = 900;
const dashboardPort = process.env["SYNAPSE_COCKPIT_E2E_PICTURE_DASHBOARD_PORT"] ?? "18775";
const hubPort = process.env["SYNAPSE_COCKPIT_E2E_PICTURE_HUB_PORT"] ?? "18776";
const localPython = resolve("../../.venv/bin/python");
const python = process.env["SYNAPSE_COCKPIT_E2E_PYTHON"] ??
  (existsSync(localPython) ? localPython : "python");

interface Started {
  readonly child: ChildProcessWithoutNullStreams;
  readonly line: string;
  readonly exited: Promise<void>;
}

/** Start one helper process and resolve with its first complete output line. */
async function start(script: string, args: readonly string[], env: NodeJS.ProcessEnv): Promise<Started> {
  const child = spawn(python, ["-u", script, ...args], { env, stdio: ["pipe", "pipe", "pipe"] });
  child.stdin.end();
  const exited = new Promise<void>(done => {
    child.once("exit", () => done());
    child.once("error", () => done());
  });
  const line = await new Promise<string>((ready, reject) => {
    let output = "";
    let errors = "";
    const deadline = setTimeout(() => reject(new Error(`${script} startup deadline: ${errors}`)), 30_000);
    child.stderr.on("data", chunk => { errors += String(chunk); });
    child.once("error", error => { clearTimeout(deadline); reject(error); });
    child.once("exit", code => {
      clearTimeout(deadline);
      reject(new Error(`${script} exited ${code}: ${errors}`));
    });
    child.stdout.on("data", chunk => {
      output += String(chunk);
      if (!output.includes("\n")) return;
      clearTimeout(deadline);
      ready(output.split("\n")[0]!);
    });
  });
  return { child, line, exited };
}

async function stop(started: Started | undefined): Promise<void> {
  if (!started) return;
  started.child.kill("SIGTERM");
  await started.exited;
}

function names(value: unknown, key: string): string[] {
  const list = (value as Record<string, unknown>)[key];
  if (!Array.isArray(list) || list.length === 0 || !list.every(item => typeof item === "string")) {
    throw new Error(`the demonstration fleet reported no ${key}`);
  }
  return list as string[];
}

test("the README picture shows a working fleet in the built cockpit", async ({ page }) => {
  test.setTimeout(90_000);
  let harness: Started | undefined;
  let fleet: Started | undefined;
  try {
    harness = await start("e2e/dashboard_harness.py", [], {
      ...process.env,
      PYTHONUNBUFFERED: "1",
      SYNAPSE_COCKPIT_E2E_DASHBOARD_PORT: dashboardPort,
      SYNAPSE_COCKPIT_E2E_HUB_PORT: hubPort,
    });
    const url = /ready at (\S+)/u.exec(harness.line)?.[1];
    if (!url) throw new Error(`unexpected harness readiness: ${harness.line}`);

    // The operator has the cockpit open before the fleet starts: the activity
    // spine draws only the changes this page itself observed.
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

    fleet = await start("e2e/demo_fleet.py", [`ws://127.0.0.1:${hubPort}`], { ...process.env });
    const seeded: unknown = JSON.parse(fleet.line);
    const participants = names(seeded, "participants");
    const tasks = names(seeded, "tasks");
    const claims = names(seeded, "claims");

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
    await stop(fleet);
    await stop(harness);
  }
});
