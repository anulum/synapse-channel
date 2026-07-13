// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — official registry VSIX with immutable listing links

import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const extensionRoot = fileURLToPath(new URL("../", import.meta.url));

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: extensionRoot,
    encoding: "utf8",
    ...options,
  });
  if (result.status !== 0) {
    throw new Error(result.stderr?.trim() || `${command} exited with status ${result.status}.`);
  }
  return result.stdout?.trim() ?? "";
}

const commit = run("git", ["rev-parse", "HEAD"]);
if (!/^[0-9a-f]{40}$/.test(commit)) {
  throw new Error("Registry package requires an exact Git commit.");
}
const status = run("git", ["status", "--porcelain=v1", "--untracked-files=all", "--", "."]);
if (status !== "") {
  throw new Error("Registry package requires a clean clients/vscode checkout at the reviewed commit.");
}

await mkdir(new URL("../dist/", import.meta.url), { recursive: true });
const npm = process.platform === "win32" ? "npm.cmd" : "npm";
const result = spawnSync(npm, [
  "exec", "--", "vsce", "package",
  "--no-dependencies",
  "--baseContentUrl", `https://github.com/anulum/synapse-channel/blob/${commit}/clients/vscode/`,
  "--baseImagesUrl", `https://raw.githubusercontent.com/anulum/synapse-channel/${commit}/clients/vscode/`,
  "--out", "dist/synapse-channel-vscode.registry.vsix",
], { cwd: extensionRoot, stdio: "inherit" });

if (result.status !== 0) {
  throw new Error(`Pinned vsce packaging failed with status ${result.status}.`);
}
