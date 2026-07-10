// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — real-hub VS Code Extension Development Host launcher

import { strict as assert } from "node:assert";
import { spawn, type ChildProcess } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer, createConnection } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { runTests, type TestOptions } from "@vscode/test-electron";

const HERE = __dirname;
const EXTENSION_ROOT = resolve(HERE, "..", "..");
const REPOSITORY_ROOT = resolve(EXTENSION_ROOT, "..", "..");
const TEST_CODE_VERSION = "1.128.0";

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
      server.close((error) => {
        if (error) {
          reject(error);
        } else {
          resolvePort(address.port);
        }
      });
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

async function stopHub(hub: ChildProcess): Promise<void> {
  if (hub.exitCode !== null || hub.signalCode !== null) {
    return;
  }
  let exited = false;
  const exit = new Promise<void>((resolveExit) => {
    hub.once("exit", () => {
      exited = true;
      resolveExit();
    });
  });
  hub.kill("SIGINT");
  await Promise.race([exit, delay(5_000)]);
  if (!exited) {
    hub.kill("SIGKILL");
    await exit;
  }
}

async function main(): Promise<void> {
  const temporary = await mkdtemp(join(tmpdir(), "synapse-vscode-auth-"));
  let hub: ChildProcess | undefined;
  let hubOutput = "";
  try {
    const workspace = join(temporary, "workspace");
    const tokenFile = join(temporary, "hub.token");
    const database = join(temporary, "hub.db");
    const userData = join(temporary, "user-data");
    const extensions = join(temporary, "extensions");
    await mkdir(workspace);
    await writeFile(join(workspace, "sample.txt"), "extension-host acceptance\n", { flag: "wx" });
    const token = `vscode-integration-${randomUUID()}`;
    await writeFile(tokenFile, token, { flag: "wx", mode: 0o600 });

    const localPython = join(REPOSITORY_ROOT, ".venv", "bin", "python");
    const python = process.env["SYNAPSE_PYTHON"]
      ?? (existsSync(localPython) ? localPython : "python3");
    const port = await reservePort();
    hub = spawn(
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
        database,
      ],
      {
        cwd: REPOSITORY_ROOT,
        env: { ...process.env, PYTHONUNBUFFERED: "1" },
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    const capture = (chunk: Buffer): void => {
      hubOutput = `${hubOutput}${chunk.toString("utf8")}`.slice(-16_000);
    };
    hub.stdout?.on("data", capture);
    hub.stderr?.on("data", capture);
    await waitForHub(hub, port, () => hubOutput);

    const options: TestOptions = {
      extensionDevelopmentPath: EXTENSION_ROOT,
      extensionTestsPath: join(HERE, "suite.cjs"),
      extensionTestsEnv: {
        SYNAPSE_VSCODE_TEST_TOKEN: token,
        SYNAPSE_VSCODE_TEST_URI: `ws://127.0.0.1:${port}`,
      },
      version: TEST_CODE_VERSION,
      cachePath: process.env["VSCODE_TEST_CACHE"]
        ?? join(tmpdir(), "synapse-vscode-test-cache"),
      launchArgs: [
        workspace,
        "--disable-extensions",
        `--user-data-dir=${userData}`,
        `--extensions-dir=${extensions}`,
      ],
    };
    const executable = process.env["VSCODE_EXECUTABLE_PATH"];
    if (executable !== undefined && executable.length > 0) {
      options.vscodeExecutablePath = executable;
    }
    assert.equal(await runTests(options), 0);
  } catch (error) {
    if (hubOutput) {
      console.error(`Integration hub output:\n${hubOutput}`);
    }
    throw error;
  } finally {
    if (hub !== undefined) {
      await stopHub(hub);
    }
    await rm(temporary, { recursive: true, force: true });
  }
}

void main().catch((error: unknown) => {
  console.error(error);
  process.exitCode = 1;
});
