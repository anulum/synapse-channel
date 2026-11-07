// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — real packaged 0.2.0 to 0.3.0 editor upgrade acceptance

import { strict as assert } from "node:assert";
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import {
  downloadAndUnzipVSCode,
  resolveCliArgsFromVSCodeExecutablePath,
} from "@vscode/test-electron";
import { startIntegrationHub, stopIntegrationHub } from "./hubHarness.cjs";
import { waitForRoster } from "./upgradeProbe.cjs";

const here = __dirname;
const extensionRoot = resolve(here, "..", "..");
const repositoryRoot = resolve(extensionRoot, "..", "..");
const codeVersion = "1.128.0";

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

function cliCommand(cli: string[], args: string[], capture = false): string {
  const [command, ...prefix] = cli;
  assert.ok(command);
  const result = spawnSync(command, [...prefix, ...args], {
    encoding: "utf8",
    stdio: capture ? "pipe" : "inherit",
  });
  assert.equal(result.status, 0, `VS Code CLI failed: ${args.join(" ")}`);
  return result.stdout?.trim() ?? "";
}

function install(cli: string[], userData: string, extensions: string, vsix: string): void {
  cliCommand(cli, [
    "--user-data-dir", userData,
    "--extensions-dir", extensions,
    "--install-extension", vsix,
    "--force",
  ]);
}

function startEditor(
  executable: string,
  workspace: string,
  userData: string,
  extensions: string,
): ChildProcess {
  const editor = spawn(executable, [
    workspace,
    "--new-window",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-updates",
    "--disable-workspace-trust",
    "--skip-welcome",
    "--skip-release-notes",
    "--password-store=basic",
    `--user-data-dir=${userData}`,
    `--extensions-dir=${extensions}`,
  ], {
    env: process.env,
    stdio: ["ignore", "pipe", "pipe"],
    // Its own process group, so stopping the editor also stops every helper it
    // started (renderers, crash handlers, anything it opened with xdg-open).
    detached: true,
  });
  editor.stdout?.pipe(process.stdout);
  editor.stderr?.pipe(process.stderr);
  return editor;
}

async function editorWindow(editor: ChildProcess): Promise<string> {
  const deadline = Date.now() + 15_000;
  do {
    assert.equal(editor.exitCode, null, "VS Code exited before its window appeared.");
    const result = spawnSync("xdotool", ["search", "--onlyvisible", "--class", "code"], { encoding: "utf8" });
    const windows = result.stdout?.trim().split(/\s+/).filter(Boolean) ?? [];
    for (const window of windows.reverse()) {
      const name = spawnSync("xdotool", ["getwindowname", window], { encoding: "utf8" }).stdout?.trim() ?? "";
      if (name.includes("Visual Studio Code")) {
        return window;
      }
    }
    await delay(150);
  } while (Date.now() < deadline);
  throw new Error("VS Code window did not appear under Xvfb.");
}

function xdotool(args: string[], input?: string): void {
  const result = spawnSync("xdotool", args, { encoding: "utf8", input });
  assert.equal(result.status, 0, `xdotool failed: ${args.join(" ")}`);
}

async function runCommand(window: string, command: string, input?: string): Promise<void> {
  xdotool(["windowfocus", "--sync", window]);
  xdotool(["key", "--clearmodifiers", "F1"]);
  await delay(600);
  xdotool(["type", "--clearmodifiers", "--delay", "8", "--file", "-"], command);
  await delay(1_000);
  xdotool(["key", "--clearmodifiers", "Return"]);
  if (input !== undefined) {
    await delay(2_000);
    xdotool(["key", "--clearmodifiers", "ctrl+a"]);
    xdotool(["type", "--clearmodifiers", "--delay", "8", "--file", "-"], input);
    await delay(300);
    xdotool(["key", "--clearmodifiers", "Return"]);
  }
  await delay(700);
}

function signalEditorGroup(editor: ChildProcess, signal: NodeJS.Signals): void {
  if (editor.pid === undefined) {
    return;
  }
  try {
    process.kill(-editor.pid, signal);
  } catch (error: unknown) {
    if ((error as NodeJS.ErrnoException).code !== "ESRCH") {
      throw error;
    }
  }
}

async function stopEditor(editor: ChildProcess): Promise<void> {
  if (editor.exitCode === null && editor.signalCode === null) {
    const exited = new Promise<void>((resolveExit) => editor.once("exit", () => resolveExit()));
    signalEditorGroup(editor, "SIGTERM");
    await Promise.race([exited, delay(10_000)]);
    if (editor.exitCode === null && editor.signalCode === null) {
      signalEditorGroup(editor, "SIGKILL");
      await exited;
    }
  }
  // A helper left in the group would keep the output pipes open and this
  // process alive after a failure, until the job is cancelled hours later.
  signalEditorGroup(editor, "SIGKILL");
  editor.stdout?.destroy();
  editor.stderr?.destroy();
}

/** Write a stand-in browser that records each URL instead of opening a window. */
async function recordingBrowser(directory: string): Promise<{ script: string; log: string }> {
  const script = join(directory, "record-browser.sh");
  const log = join(directory, "external-urls.log");
  await writeFile(script, `#!/bin/sh\nprintf '%s\\n' "$*" >> '${log}'\n`, { flag: "wx" });
  await chmod(script, 0o700);
  return { script, log };
}

async function openedUrls(log: string): Promise<string> {
  try {
    return (await readFile(log, "utf8")).trim() || "none";
  } catch {
    return "none";
  }
}

function windowReport(): string {
  const visible = spawnSync("xdotool", ["search", "--onlyvisible", "--name", "."], { encoding: "utf8" });
  const names = (visible.stdout?.trim().split(/\s+/).filter(Boolean) ?? []).map((window) => {
    const name = spawnSync("xdotool", ["getwindowname", window], { encoding: "utf8" }).stdout?.trim();
    return `  ${window}: ${name ?? ""}`;
  });
  const focused = spawnSync("xdotool", ["getwindowfocus", "getwindowname"], { encoding: "utf8" });
  return [`Focused window: ${focused.stdout?.trim() ?? "unknown"}`, "Visible windows:", ...names].join("\n");
}

async function main(): Promise<void> {
  const oldVsix = process.env["SYNAPSE_VSCODE_OLD_VSIX"];
  const currentVsix = process.env["SYNAPSE_VSCODE_CURRENT_VSIX"];
  assert.ok(oldVsix && currentVsix, "Both upgrade VSIX paths are required.");
  assert.equal(spawnSync("xdotool", ["version"]).status, 0, "xdotool is required.");
  const temporary = await mkdtemp(join(tmpdir(), "synapse-vscode-upgrade-"));
  const workspace = join(temporary, "workspace");
  const userData = join(temporary, "user-data");
  const extensions = join(temporary, "extensions");
  await mkdir(join(workspace, ".git"), { recursive: true });
  await mkdir(join(userData, "User"), { recursive: true });
  await mkdir(extensions, { recursive: true });
  await writeFile(join(workspace, "sample.txt"), "stateful upgrade acceptance\n", { flag: "wx" });
  const identity = "synapse-upgrade/vscode";
  const localPython = join(repositoryRoot, ".venv", "bin", "python");
  const python = process.env["SYNAPSE_PYTHON"] ?? (existsSync(localPython) ? localPython : "python3");
  const hub = await startIntegrationHub(temporary, "upgrade", python, repositoryRoot);
  let editor: ChildProcess | undefined;
  try {
    // A host browser (Chrome's first-run terms window on Ubuntu 26.04 runners) took the
    // focus while the command palette was typed into; the editor records URLs instead.
    const browser = await recordingBrowser(temporary);
    const settings = {
      "synapse.hubUri": hub.uri,
      "synapse.identity": identity,
      "workbench.externalBrowser": browser.script,
    };
    await writeFile(join(userData, "User", "settings.json"), JSON.stringify(settings));
    const cachePath = process.env["VSCODE_TEST_CACHE"] ?? join(extensionRoot, ".vscode-test-cache");
    const executable = await downloadAndUnzipVSCode({ version: codeVersion, cachePath });
    const cli = resolveCliArgsFromVSCodeExecutablePath(executable, { reuseMachineInstall: true });

    install(cli, userData, extensions, oldVsix);
    assert.match(cliCommand(cli, ["--user-data-dir", userData, "--extensions-dir", extensions, "--list-extensions", "--show-versions"], true), /anulum\.synapse-channel-vscode@0\.2\.0/);
    editor = startEditor(executable, workspace, userData, extensions);
    const oldWindow = await editorWindow(editor);
    await delay(10_000);
    await runCommand(oldWindow, "SYNAPSE: Set hub token", hub.token);
    await waitForRoster(hub.uri, hub.token, identity, true);
    console.log("SYNAPSE_VSCODE_020_STATE_SEEDED_PASS");
    await stopEditor(editor);
    editor = undefined;

    install(cli, userData, extensions, currentVsix);
    assert.match(cliCommand(cli, ["--user-data-dir", userData, "--extensions-dir", extensions, "--list-extensions", "--show-versions"], true), /anulum\.synapse-channel-vscode@0\.3\.0/);
    assert.deepEqual(JSON.parse(await readFile(join(userData, "User", "settings.json"), "utf8")), settings);
    editor = startEditor(executable, workspace, userData, extensions);
    const currentWindow = await editorWindow(editor);
    await waitForRoster(hub.uri, hub.token, identity, true);
    await runCommand(currentWindow, "SYNAPSE: Clear hub token");
    await waitForRoster(hub.uri, hub.token, identity, false);
    console.log("SYNAPSE_VSCODE_020_TO_030_STATEFUL_UPGRADE_PASS");
  } catch (error: unknown) {
    if (editor !== undefined) {
      console.error(windowReport());
    }
    console.error(`External URLs the editor asked to open: ${await openedUrls(join(temporary, "external-urls.log"))}`);
    throw error;
  } finally {
    if (editor !== undefined) {
      await stopEditor(editor);
    }
    await stopIntegrationHub(hub);
    if (process.env["SYNAPSE_KEEP_UPGRADE_TEMP"] === "1") {
      console.error(`Preserved upgrade profile for diagnosis: ${temporary}`);
    } else {
      await rm(temporary, { recursive: true, force: true });
    }
  }
}

main().then(
  () => process.exit(0),
  (error: unknown) => {
    console.error(error);
    process.exit(1);
  },
);
