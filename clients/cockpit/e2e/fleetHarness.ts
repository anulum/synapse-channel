// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — a private hub, dashboard and demonstration fleet for one journey

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

// A journey that shows a fleet does not use the suite's shared harness: the
// fleet's plan and claims would stay in that hub for the journeys after it.
export const dashboardPort = process.env["SYNAPSE_COCKPIT_E2E_PICTURE_DASHBOARD_PORT"] ?? "18775";
export const hubPort = process.env["SYNAPSE_COCKPIT_E2E_PICTURE_HUB_PORT"] ?? "18776";
const localPython = resolve("../../.venv/bin/python");
const python = process.env["SYNAPSE_COCKPIT_E2E_PYTHON"] ??
  (existsSync(localPython) ? localPython : "python");

export interface Started {
  readonly child: ChildProcessWithoutNullStreams;
  readonly line: string;
  readonly exited: Promise<void>;
}

/** Start one helper process and resolve with its first complete output line. */
export async function start(script: string, args: readonly string[], env: NodeJS.ProcessEnv): Promise<Started> {
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

export async function stop(started: Started | undefined): Promise<void> {
  if (!started) return;
  started.child.kill("SIGTERM");
  await started.exited;
}

/** Start the private hub and dashboard and return the cockpit's address. */
export async function startHarness(): Promise<{ readonly harness: Started; readonly url: string }> {
  const harness = await start("e2e/dashboard_harness.py", [], {
    ...process.env,
    PYTHONUNBUFFERED: "1",
    SYNAPSE_COCKPIT_E2E_DASHBOARD_PORT: dashboardPort,
    SYNAPSE_COCKPIT_E2E_HUB_PORT: hubPort,
  });
  const url = /ready at (\S+)/u.exec(harness.line)?.[1];
  if (!url) {
    await stop(harness);
    throw new Error(`unexpected harness readiness: ${harness.line}`);
  }
  return { harness, url };
}

export interface Fleet {
  readonly started: Started;
  readonly participants: readonly string[];
  readonly tasks: readonly string[];
  readonly claims: readonly string[];
}

function names(value: unknown, key: string): string[] {
  const list = (value as Record<string, unknown>)[key];
  if (!Array.isArray(list) || list.length === 0 || !list.every(item => typeof item === "string")) {
    throw new Error(`the demonstration fleet reported no ${key}`);
  }
  return list as string[];
}

/** Start the demonstration fleet against the private hub and read what it seeded. */
export async function startFleet(options: readonly string[] = []): Promise<Fleet> {
  const started = await start("e2e/demo_fleet.py", [...options, `ws://127.0.0.1:${hubPort}`], { ...process.env });
  try {
    const seeded: unknown = JSON.parse(started.line);
    return {
      started,
      participants: names(seeded, "participants"),
      tasks: names(seeded, "tasks"),
      claims: names(seeded, "claims"),
    };
  } catch (error) {
    await stop(started);
    throw error;
  }
}
