// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — real-hub VS Code Extension Development Host launcher

import { strict as assert } from "node:assert";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { runTests, type TestOptions } from "@vscode/test-electron";
import {
  type IntegrationHub,
  startIntegrationHub,
  stopIntegrationHub,
} from "./hubHarness.cjs";

const HERE = __dirname;
const EXTENSION_ROOT = resolve(HERE, "..", "..");
const REPOSITORY_ROOT = resolve(EXTENSION_ROOT, "..", "..");
const TEST_CODE_VERSION = "1.128.0";

async function main(): Promise<void> {
  const temporary = await mkdtemp(join(tmpdir(), "synapse-vscode-auth-"));
  const hubs: IntegrationHub[] = [];
  try {
    const workspace = join(temporary, "primary-worktree");
    const secondWorkspace = join(temporary, "secondary-worktree");
    const workspaceFile = join(temporary, "multi-root.code-workspace");
    const userData = join(temporary, "user-data");
    const extensions = join(temporary, "extensions");
    await mkdir(join(workspace, ".git"), { recursive: true });
    await mkdir(join(secondWorkspace, ".git"), { recursive: true });
    await writeFile(join(workspace, "sample.txt"), "extension-host acceptance\n", { flag: "wx" });
    await writeFile(join(workspace, "second.txt"), "exact release acceptance\n", { flag: "wx" });
    await writeFile(
      join(secondWorkspace, "sample.txt"),
      "multi-root worktree acceptance\n",
      { flag: "wx" },
    );
    await writeFile(
      workspaceFile,
      JSON.stringify({ folders: [{ path: workspace }, { path: secondWorkspace }] }),
      { flag: "wx" },
    );
    const localPython = join(REPOSITORY_ROOT, ".venv", "bin", "python");
    const python = process.env["SYNAPSE_PYTHON"]
      ?? (existsSync(localPython) ? localPython : "python3");
    hubs.push(
      await startIntegrationHub(temporary, "primary", python, REPOSITORY_ROOT),
      await startIntegrationHub(temporary, "secondary", python, REPOSITORY_ROOT),
    );
    const [primary, secondary] = hubs;
    assert.ok(primary && secondary);

    const options: TestOptions = {
      extensionDevelopmentPath: EXTENSION_ROOT,
      extensionTestsPath: join(HERE, "suite.cjs"),
      extensionTestsEnv: {
        SYNAPSE_VSCODE_TEST_TOKEN: primary.token,
        SYNAPSE_VSCODE_TEST_URI: primary.uri,
        SYNAPSE_VSCODE_TEST_TOKEN_2: secondary.token,
        SYNAPSE_VSCODE_TEST_URI_2: secondary.uri,
      },
      version: TEST_CODE_VERSION,
      cachePath: process.env["VSCODE_TEST_CACHE"]
        ?? join(EXTENSION_ROOT, ".vscode-test-cache"),
      launchArgs: [
        workspaceFile,
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
    for (const hub of hubs) {
      if (hub.output()) {
        console.error(`Integration hub ${hub.uri} output:\n${hub.output()}`);
      }
    }
    throw error;
  } finally {
    for (const hub of hubs) {
      await stopIntegrationHub(hub);
    }
    await rm(temporary, { recursive: true, force: true });
  }
}

void main().catch((error: unknown) => {
  console.error(error);
  process.exitCode = 1;
});
