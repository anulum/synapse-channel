// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — Core file grants and Fleet panel keyboard continuity

import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync, mkdtempSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

async function refresh(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await (window as typeof window & { SynapseStudioFleet: { refresh(): Promise<void> } })
      .SynapseStudioFleet.refresh();
  });
}

test("file-backed grants, changed exports and keyboard selection use real Core HTTP", async ({ page, request }) => {
  const root = mkdtempSync(join(tmpdir(), "synapse-fleet-files-"));
  const bearer = randomUUID();
  const viewer = randomUUID();
  function publish(name: string, value: unknown): void {
    const pending = join(root, `${name}.next`);
    writeFileSync(pending, JSON.stringify(value), { mode: 0o600 });
    renameSync(pending, join(root, name));
  }
  writeFileSync(join(root, "observer.token"), bearer, { mode: 0o600 });
  writeFileSync(join(root, "viewer.token"), viewer, { mode: 0o600 });
  publish("access.json", { version: 1, principals: [
    { id: "observer", role: "viewer", token_file: "observer.token" },
    { id: "viewer", role: "viewer", token_file: "viewer.token" },
  ] });
  publish("grants.json", { version: 1, observers: ["observer"] });
  const peer = { peer_id: "hub-b", cursor: 1, events: 1, last_success_at: null,
    consecutive_failures: null, status_written_at: null, caught_up: false,
    budget_exhausted_reason: "pages" };
  const doc = { version: 1, source_id: "browser-lab", exported_at: 20, advisory: true,
    snapshot: { advisory: "observations grant no authority", generated_at: 19,
      peers: [peer], progress_notes: 0, tasks: [{ task_id: "shared", status: "open",
        title: "Conflicting observations", claimed_by: null, claim_hub: null,
        board_provenance: { hub_id: "hub-b", authoritative: false },
        board_conflict: { status: "unresolved", contenders: [{ hub_id: "hub-c" }] },
      }] } };
  publish("mirror.json", doc);
  const localPython = resolve("../../.venv/bin/python");
  const python = process.env["SYNAPSE_COCKPIT_E2E_PYTHON"] ??
    (existsSync(localPython) ? localPython : "python");
  const server = spawn(python, ["-u", "e2e/fleet_dashboard_harness.py", root], {
    stdio: ["ignore", "pipe", "pipe"],
  });
  const exited = new Promise<void>(resolveExit => {
    server.once("exit", () => resolveExit());
    server.once("error", () => resolveExit());
  });
  try {
    const url = await new Promise<string>((resolveUrl, reject) => {
      let output = "";
      let errors = "";
      const deadline = setTimeout(() => reject(new Error("Fleet fixture startup deadline")), 10_000);
      server.stderr.on("data", chunk => { errors += String(chunk); });
      server.once("error", error => { clearTimeout(deadline); reject(error); });
      server.once("exit", code => {
        clearTimeout(deadline);
        reject(new Error(`Fleet fixture exited ${code}: ${errors}`));
      });
      server.stdout.on("data", chunk => {
        output += String(chunk);
        if (!output.includes("\n")) return;
        clearTimeout(deadline);
        try {
          const ready: unknown = JSON.parse(output.split("\n")[0]!);
          if (!ready || typeof ready !== "object" || !("url" in ready) || typeof ready.url !== "string") {
            throw new Error("Invalid fixture readiness");
          }
          resolveUrl(ready.url);
        } catch (error) { reject(error); }
      });
    });
    const feed = new URL("/fleet-observed.json", url).href;
    const headers = { Authorization: `Bearer ${bearer}` };
    expect((await request.get(feed)).status()).toBe(401);
    const viewerHeaders = { Authorization: `Bearer ${viewer}` };
    expect((await request.get(url, { headers: viewerHeaders })).status()).toBe(200);
    expect((await request.get(feed, { headers: viewerHeaders })).status()).toBe(403);
    const accepted = await request.get(feed, { headers });
    expect(accepted.status()).toBe(200);
    expect(await accepted.json()).toEqual(doc);
    expect(accepted.headers()["cache-control"]).toContain("no-store");
    const access = await request.get(new URL("/fleet-observed-access.json", url).href, { headers });
    expect(access.status()).toBe(200);
    expect(await access.json()).toEqual({ version: 1, observe: true, advisory: true });
    await page.setExtraHTTPHeaders(headers);
    await page.goto(url);
    const panel = page.getByRole("region", { name: "Fleet mirror", exact: true });
    const summary = panel.locator("summary");
    await expect(summary).toContainText("unresolved conflict");
    await summary.focus();
    await page.keyboard.press("Enter");
    await expect(panel.locator("pre")).toContainText("hub-c");
    doc.exported_at += 1;
    doc.snapshot.tasks[0]!.status = "done";
    doc.snapshot.peers = Array.from({ length: 51 }, (_, i) => ({ ...peer, peer_id: `peer-${i}` }));
    publish("mirror.json", doc);
    await refresh(page);
    await expect(summary).toBeFocused();
    await expect(panel.locator("details")).toHaveAttribute("open", "");
    await expect(panel.locator("pre")).toContainText('"status": "done"');
    await expect(panel).toContainText("Rows 51–52 of 52");
    doc.source_id = "second-lab";
    publish("mirror.json", doc);
    await refresh(page);
    const status = panel.getByRole("status");
    await expect(status).toBeFocused();
    await panel.getByRole("button", { name: "Next mirror rows" }).focus();
    await page.keyboard.press("Enter");
    await expect(panel.locator("details")).not.toHaveAttribute("open", "");
    await summary.focus();
    await page.keyboard.press("Space");
    await expect(panel.locator("pre")).toContainText("hub-c");
    publish("grants.json", { version: 1, observers: [] });
    expect((await request.get(feed, { headers })).status()).toBe(403);
    await refresh(page);
    await expect(status).toHaveText("locked");
    await expect(status).toBeFocused();
    await expect(summary).toHaveCount(0);
    await expect(panel).not.toContainText("hub-c");
    publish("grants.json", { version: 1, observers: ["observer"] });
    await refresh(page);
    await expect(panel).toContainText("second-lab");
    await panel.getByRole("button", { name: "Next mirror rows" }).focus();
    await page.keyboard.press("Enter");
    await expect(summary).toContainText("shared");
    await expect(panel.locator("details")).not.toHaveAttribute("open", "");
  } finally {
    try {
      await page.close();
    } finally {
      server.kill("SIGTERM");
      const deadline = setTimeout(() => server.kill("SIGKILL"), 5000);
      await exited;
      clearTimeout(deadline);
      rmSync(root, { recursive: true });
    }
  }
});
