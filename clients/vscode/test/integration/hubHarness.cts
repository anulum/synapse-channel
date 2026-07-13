// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — disposable real-hub Extension Host harness

import { spawn, type ChildProcess } from "node:child_process";
import { randomUUID } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { createConnection, createServer } from "node:net";
import { join } from "node:path";

/** One disposable token-gated hub used by Extension Host acceptance. */
export interface IntegrationHub {
  process: ChildProcess;
  token: string;
  uri: string;
  output(): string;
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

async function reservePort(): Promise<number> {
  return await new Promise<number>((resolvePort, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (address === null || typeof address === "string") {
        server.close();
        reject(new Error("Could not reserve an IPv4 port for the integration hub."));
        return;
      }
      server.close((error) => error ? reject(error) : resolvePort(address.port));
    });
  });
}

async function probeTcp(port: number): Promise<void> {
  await new Promise<void>((resolveProbe, reject) => {
    const socket = createConnection({ host: "127.0.0.1", port });
    socket.setTimeout(500);
    socket.once("connect", () => {
      socket.destroy();
      resolveProbe();
    });
    socket.once("timeout", () => {
      socket.destroy();
      reject(new Error("TCP readiness probe timed out."));
    });
    socket.once("error", reject);
  });
}

async function waitForHub(hub: ChildProcess, port: number, output: () => string): Promise<void> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (hub.exitCode !== null || hub.signalCode !== null) {
      throw new Error(`Integration hub exited before readiness.\n${output()}`);
    }
    try {
      await probeTcp(port);
      return;
    } catch {
      await delay(100);
    }
  }
  throw new Error(`Integration hub did not become ready.\n${output()}`);
}

/** Start a separate token, database, port, and bounded diagnostic buffer. */
export async function startIntegrationHub(
  temporary: string,
  label: string,
  python: string,
  repositoryRoot: string,
): Promise<IntegrationHub> {
  const token = `vscode-integration-${label}-${randomUUID()}`;
  const tokenFile = join(temporary, `${label}.token`);
  await writeFile(tokenFile, token, { flag: "wx", mode: 0o600 });
  const port = await reservePort();
  const process = spawn(
    python,
    [
      "-m",
      "synapse_channel.cli",
      "hub",
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
      "--token-file",
      tokenFile,
      "--db",
      join(temporary, `${label}.db`),
    ],
    {
      cwd: repositoryRoot,
      env: { ...globalThis.process.env, PYTHONUNBUFFERED: "1" },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  let captured = "";
  const capture = (chunk: Buffer): void => {
    captured = `${captured}${chunk.toString("utf8")}`.slice(-16_000);
  };
  process.stdout?.on("data", capture);
  process.stderr?.on("data", capture);
  const output = (): string => captured;
  await waitForHub(process, port, output);
  return { process, token, uri: `ws://127.0.0.1:${port}`, output };
}

/** Stop a disposable hub gracefully, then bound teardown with SIGKILL. */
export async function stopIntegrationHub(hub: IntegrationHub): Promise<void> {
  if (hub.process.exitCode !== null || hub.process.signalCode !== null) {
    return;
  }
  let exited = false;
  const exit = new Promise<void>((resolveExit) => {
    hub.process.once("exit", () => {
      exited = true;
      resolveExit();
    });
  });
  hub.process.kill("SIGINT");
  await Promise.race([exit, delay(5_000)]);
  if (!exited) {
    hub.process.kill("SIGKILL");
    await exit;
  }
}
