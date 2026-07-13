// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — Marketplace/Open VSX package policy tests

import { describe, expect, it } from "vitest";
import { unzipSync, zipSync } from "fflate";
import { PNG } from "pngjs";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  readPngDimensions,
  verifyMarketplaceArchive,
} from "../scripts/verify-marketplace-package.mjs";
import { repackVsix } from "../scripts/repack-vsix.mjs";

const commit = "0123456789abcdef0123456789abcdef01234567";
const text = (value: string): Uint8Array => new TextEncoder().encode(value);

function png(width: number, height: number): Uint8Array {
  const image = new PNG({ width, height });
  return PNG.sync.write(image);
}

const iconPng = png(256, 256);
const screenshotPng = png(1440, 900);

function manifest(overrides: Record<string, unknown> = {}) {
  return {
    name: "synapse-channel-vscode",
    publisher: "anulum",
    version: "0.3.0",
    displayName: "SYNAPSE CHANNEL",
    description: "Claim files, see ownership, and watch live hub coordination from the editor.",
    license: "AGPL-3.0-or-later",
    icon: "media/icon.png",
    main: "./out/extension.js",
    homepage: "https://github.com/anulum/synapse-channel/tree/main/clients/vscode#readme",
    repository: { url: "https://github.com/anulum/synapse-channel.git" },
    bugs: { url: "https://github.com/anulum/synapse-channel/issues" },
    engines: { vscode: "^1.90.0" },
    extensionKind: ["workspace"],
    categories: ["SCM Providers"],
    keywords: ["coordination"],
    galleryBanner: { color: "#0c1118", theme: "dark" },
    preview: true,
    pricing: "Free",
    capabilities: {
      virtualWorkspaces: {
        supported: false,
        description: "Canonical filesystem worktrees are required.",
      },
      untrustedWorkspaces: {
        supported: false,
        description: "Trust the workspace before connecting it to a hub.",
      },
    },
    ...overrides,
  };
}

function archive(overrides: Record<string, Uint8Array> = {}): Uint8Array {
  return zipSync({
    "extension/package.json": text(JSON.stringify(manifest())),
    "extension/readme.md": text([
      "Version 0.3.0. Registry availability is established only by that registry's listing.",
      `[privacy notice](https://github.com/anulum/synapse-channel/blob/${commit}/clients/vscode/PRIVACY.md)`,
      `[support policy](https://github.com/anulum/synapse-channel/blob/${commit}/clients/vscode/SUPPORT.md)`,
      "[security policy](https://example.invalid/security)",
      `[licence](https://github.com/anulum/synapse-channel/blob/${commit}/clients/vscode/LICENSE)`,
      `![icon](https://raw.githubusercontent.com/anulum/synapse-channel/${commit}/clients/vscode/media/icon.png)`,
      `![board](https://raw.githubusercontent.com/anulum/synapse-channel/${commit}/clients/vscode/media/marketplace-board.png)`,
      `![claims](https://raw.githubusercontent.com/anulum/synapse-channel/${commit}/clients/vscode/media/marketplace-claims.png)`,
    ].join("\n")),
    "extension/changelog.md": text("## 0.3.0\n"),
    "extension/SUPPORT.md": text("issue tracker and security policy"),
    "extension/PRIVACY.md": text("no analytics; VS Code SecretStorage"),
    "extension/LICENSE.txt": text("AGPL-3.0-or-later"),
    "extension/media/icon.png": iconPng,
    "extension/media/marketplace-board.png": screenshotPng,
    "extension/media/marketplace-claims.png": screenshotPng,
    "extension/out/extension.js": text("export {};"),
    ...overrides,
  });
}

describe("Marketplace package policy", () => {
  it("decodes complete PNG data", () => {
    expect(readPngDimensions(screenshotPng)).toEqual({ width: 1440, height: 900 });
  });

  it("accepts a bounded submission payload", () => {
    expect(verifyMarketplaceArchive(archive())).toMatchObject({
      identity: "anulum.synapse-channel-vscode@0.3.0",
    });
  });

  it("rejects non-HTTPS listing imagery", () => {
    const readme = new TextEncoder().encode("not yet published privacy notice support policy security policy licence\n![a](http://example.invalid/a.png)\n![b](https://example.invalid/b.png)\n![c](https://example.invalid/c.png)");
    expect(() => verifyMarketplaceArchive(archive({ "extension/readme.md": readme }))).toThrow("must use HTTPS");
  });

  it("rejects development trees", () => {
    expect(() => verifyMarketplaceArchive(archive({ "extension/src/secret.ts": new Uint8Array() }))).toThrow("development-only tree");
  });

  it("rejects credential-shaped files", () => {
    expect(() => verifyMarketplaceArchive(archive({ "extension/release.token": new Uint8Array() }))).toThrow("credential-shaped file");
  });

  it("rejects package-manager credential files", () => {
    expect(() => verifyMarketplaceArchive(archive({ "extension/.npmrc": text("//registry.npmjs.org/:_authToken=audit-secret-value") }))).toThrow("credential-shaped file");
  });

  it("rejects literal credentials in ordinary text files", () => {
    expect(() => verifyMarketplaceArchive(archive({ "extension/notes.txt": text("api_key=literal-audit-secret") }))).toThrow("literal credential assignment");
  });

  it("rejects credential-bearing text that is not valid UTF-8", () => {
    const content = new Uint8Array([...text("api_key=literal-audit-secret"), 0xff]);
    expect(() => verifyMarketplaceArchive(archive({ "extension/notes.txt": content }))).toThrow("invalid UTF-8");
  });

  it("rejects truncated PNG payloads", () => {
    expect(() => verifyMarketplaceArchive(archive({ "extension/media/icon.png": iconPng.subarray(0, 24) }))).toThrow("not a complete valid PNG");
  });

  it("rejects a manifest entry point that is not packaged", () => {
    const packageJson = text(JSON.stringify(manifest({ main: "./out/missing.js" })));
    expect(() => verifyMarketplaceArchive(archive({ "extension/package.json": packageJson }))).toThrow("entry point drifted");
  });

  it("preserves the official registry archive while creating the deterministic copy", async () => {
    const directory = await mkdtemp(join(tmpdir(), "synapse-vsix-"));
    const input = join(directory, "registry.vsix");
    const output = join(directory, "deterministic.vsix");
    const source = archive();
    try {
      await writeFile(input, source);
      await repackVsix(input, output, "1783900800");
      expect(new Uint8Array(await readFile(input))).toEqual(source);
      const deterministic = new Uint8Array(await readFile(output));
      const officialEntries = unzipSync(source);
      const deterministicEntries = unzipSync(deterministic);
      expect(Object.keys(deterministicEntries).sort()).toEqual(Object.keys(officialEntries).sort());
      for (const name of Object.keys(officialEntries)) {
        expect(deterministicEntries[name]).toEqual(officialEntries[name]);
      }
      expect(verifyMarketplaceArchive(deterministic)).toMatchObject({
        identity: "anulum.synapse-channel-vscode@0.3.0",
      });
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
});
