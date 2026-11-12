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
  /** The first complete line the process printed. */
  readonly line: string;
  /** Resolve with the next complete line the process prints. */
  readonly nextLine: () => Promise<string>;
  readonly exited: Promise<void>;
}

/**
 * Start one helper process and resolve with its first complete output line.
 * Its standard input is closed at once unless `keepInput` is set.
 */
export async function start(
  script: string,
  args: readonly string[],
  env: NodeJS.ProcessEnv,
  keepInput = false,
): Promise<Started> {
  const child = spawn(python, ["-u", script, ...args], { env, stdio: ["pipe", "pipe", "pipe"] });
  if (!keepInput) child.stdin.end();
  const exited = new Promise<void>(done => {
    child.once("exit", () => done());
    child.once("error", () => done());
  });
  const lines: string[] = [];
  const waiting: { take: (line: string) => void; fail: (error: Error) => void }[] = [];
  let partial = "";
  let errors = "";
  let ended: Error | undefined;
  const end = (error: Error): void => {
    ended = error;
    for (const waiter of waiting.splice(0)) waiter.fail(error);
  };
  child.stderr.on("data", chunk => { errors += String(chunk); });
  child.once("error", error => end(error));
  child.once("exit", code => end(new Error(`${script} exited ${code}: ${errors}`)));
  child.stdout.on("data", chunk => {
    const parts = (partial + String(chunk)).split("\n");
    partial = parts.pop()!;
    for (const part of parts) {
      const waiter = waiting.shift();
      if (waiter) waiter.take(part);
      else lines.push(part);
    }
  });
  const nextLine = (): Promise<string> => new Promise<string>((take, fail) => {
    const ready = lines.shift();
    if (ready !== undefined) {
      take(ready);
      return;
    }
    if (ended) {
      fail(ended);
      return;
    }
    const deadline = setTimeout(() => fail(new Error(`${script} line deadline: ${errors}`)), 30_000);
    waiting.push({
      take: line => { clearTimeout(deadline); take(line); },
      fail: error => { clearTimeout(deadline); fail(error); },
    });
  });
  return { child, line: await nextLine(), nextLine, exited };
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

export interface Seeded {
  readonly participants: readonly string[];
  readonly tasks: readonly string[];
  /** The claims held when the line was printed. */
  readonly claims: readonly string[];
}

export interface Fleet extends Seeded {
  readonly started: Started;
  /**
   * Begin the fleet's second act and resolve with what it then holds. Only a
   * fleet started with `--two-acts` pauses for it.
   */
  readonly secondAct: () => Promise<Seeded>;
}

function names(value: unknown, key: string): string[] {
  const list = (value as Record<string, unknown>)[key];
  if (!Array.isArray(list) || list.length === 0 || !list.every(item => typeof item === "string")) {
    throw new Error(`the demonstration fleet reported no ${key}`);
  }
  return list as string[];
}

function seeded(line: string, act?: number): Seeded {
  const value: unknown = JSON.parse(line);
  if (act !== undefined && (value as Record<string, unknown>)["act"] !== act) {
    throw new Error(`the demonstration fleet did not report act ${act}: ${line}`);
  }
  return {
    participants: names(value, "participants"),
    tasks: names(value, "tasks"),
    claims: names(value, "claims"),
  };
}

/**
 * Start the demonstration fleet against the private hub and read what it
 * seeded. With `--two-acts` it resolves after the first act, the standing
 * state a cockpit finds, and `secondAct` lets the fleet go on.
 */
export async function startFleet(options: readonly string[] = []): Promise<Fleet> {
  const twoActs = options.includes("--two-acts");
  const started = await start(
    "e2e/demo_fleet.py",
    [...options, `ws://127.0.0.1:${hubPort}`],
    { ...process.env },
    twoActs,
  );
  try {
    const first = seeded(started.line, twoActs ? 1 : undefined);
    const secondAct = async (): Promise<Seeded> => {
      if (!twoActs) throw new Error("the fleet was not started in two acts");
      started.child.stdin.write("second act\n");
      return seeded(await started.nextLine(), 2);
    };
    return { started, ...first, secondAct };
  } catch (error) {
    await stop(started);
    throw error;
  }
}
