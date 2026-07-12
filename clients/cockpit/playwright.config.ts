// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — production-build cockpit browser gate

import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";

import { defineConfig, devices } from "@playwright/test";

const host = "127.0.0.1";
const dashboardPort = process.env["SYNAPSE_COCKPIT_E2E_DASHBOARD_PORT"] ?? "18765";
const bearer = process.env["SYNAPSE_COCKPIT_E2E_TOKEN"] ?? `cockpit-e2e-${randomUUID()}`;
const viewerBearer = process.env["SYNAPSE_COCKPIT_E2E_VIEWER_TOKEN"] ?? `cockpit-viewer-${randomUUID()}`;
const adminBearer = process.env["SYNAPSE_COCKPIT_E2E_ADMIN_TOKEN"] ?? `cockpit-admin-${randomUUID()}`;
const localPython = "../../.venv/bin/python";
const python = process.env["SYNAPSE_COCKPIT_E2E_PYTHON"] ??
  (existsSync(localPython) ? localPython : "python");

process.env["SYNAPSE_COCKPIT_E2E_TOKEN"] = bearer;
process.env["SYNAPSE_COCKPIT_E2E_VIEWER_TOKEN"] = viewerBearer;
process.env["SYNAPSE_COCKPIT_E2E_ADMIN_TOKEN"] = adminBearer;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 6_000 },
  outputDir: "test-results",
  reporter: process.env["CI"]
    ? [["line"], ["html", { open: "never", outputFolder: "playwright-report" }]]
    : "line",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: `http://${host}:${dashboardPort}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  webServer: {
    command: `${python} e2e/dashboard_harness.py`,
    url: `http://${host}:${dashboardPort}/cockpit/`,
    timeout: 20_000,
    reuseExistingServer: process.env["CI"] === undefined,
    env: {
      ...process.env,
      PYTHONUNBUFFERED: "1",
      SYNAPSE_COCKPIT_E2E_TOKEN: bearer,
      SYNAPSE_COCKPIT_E2E_VIEWER_TOKEN: viewerBearer,
      SYNAPSE_COCKPIT_E2E_ADMIN_TOKEN: adminBearer,
      SYNAPSE_COCKPIT_E2E_DASHBOARD_PORT: dashboardPort,
    },
  },
});
