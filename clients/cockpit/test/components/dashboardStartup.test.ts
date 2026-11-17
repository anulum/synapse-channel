// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the dashboard startup hang guard rejects a short revert

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { afterEach, expect, it } from "vitest";
import { DASHBOARD_START_HANG_GUARD_MS, waitForDashboardUrl } from "./dashboardStartup";

const children: ChildProcessWithoutNullStreams[] = [];

function start(program: string): ChildProcessWithoutNullStreams {
  const child = spawn(process.execPath, ["-e", program], { stdio: ["pipe", "pipe", "pipe"] });
  children.push(child);
  return child;
}

afterEach(() => {
  for (const child of children.splice(0)) {
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
  }
});

// Holds its URL until the test writes to stdin, which is after the old budget.
const SLOW_START = `
process.stderr.write("still starting the dashboard\\n");
process.stdin.on("data", () => {
  process.stdout.write("http://127.0.0.1:9/\\n");
});
setInterval(() => {}, 2147483647);
`;

const SILENT_START = `
process.stderr.write("refused to bind the dashboard\\n");
setInterval(() => {}, 2147483647);
`;

it("a start slower than five seconds still returns its URL", async () => {
  const child = start(SLOW_START);
  let diagnostics = "";
  child.stderr.on("data", (chunk: Buffer) => { diagnostics += chunk.toString(); });
  let failure: Error | undefined;
  // No budget argument: this is the guard the suite uses. The wait below is on
  // the same event loop, so a five-second revert fires before the child prints.
  const pending = waitForDashboardUrl(child, () => diagnostics).then(
    (url) => url,
    (error: unknown) => {
      failure = error instanceof Error ? error : new Error(String(error));
      return "";
    },
  );
  await new Promise<void>((resolve) => { setTimeout(resolve, 6_000); });
  if (failure !== undefined) {
    throw new Error(`the hang guard fired before the delayed print: ${failure.message}`);
  }
  child.stdin.write("go\n");
  expect(await pending).toBe("http://127.0.0.1:9/");
  expect(DASHBOARD_START_HANG_GUARD_MS).toBeGreaterThan(5_000);
}, 20_000);

it("a start that never prints fails and keeps the child stderr", async () => {
  const child = start(SILENT_START);
  let diagnostics = "";
  const heard = new Promise<void>((resolve) => {
    child.stderr.once("data", (chunk: Buffer) => {
      diagnostics += chunk.toString();
      resolve();
    });
  });
  await heard;
  // The short budget only opens the timeout message. The suite omits it.
  const caught: unknown = await waitForDashboardUrl(child, () => diagnostics, 300).then(
    () => { throw new Error("a silent start returned a URL"); },
    (error: unknown) => error,
  );
  if (!(caught instanceof Error)) throw new Error("the silent start did not fail");
  expect(caught.message).toContain("dashboard startup timed out");
  expect(caught.message).toContain("refused to bind the dashboard");
});
