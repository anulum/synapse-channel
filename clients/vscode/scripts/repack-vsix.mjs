// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — deterministic VSIX archive normaliser

import { readFile, unlink, writeFile } from "node:fs/promises";
import { unzipSync, zipSync } from "fflate";

const [, , inputPath, outputPath] = process.argv;
if (!inputPath || !outputPath || inputPath === outputPath) {
  throw new Error("Usage: repack-vsix.mjs INPUT.vsix OUTPUT.vsix");
}

const sourceDateEpoch = process.env.SOURCE_DATE_EPOCH ?? "1783900800";
if (!/^\d+$/.test(sourceDateEpoch)) {
  throw new Error("SOURCE_DATE_EPOCH must be a non-negative integer.");
}
const mtime = new Date(Number(sourceDateEpoch) * 1_000);
if (Number.isNaN(mtime.valueOf()) || mtime.getUTCFullYear() < 1980) {
  throw new Error("SOURCE_DATE_EPOCH must be a valid ZIP timestamp from 1980 onward.");
}

const unpacked = unzipSync(new Uint8Array(await readFile(inputPath)));
const entries = Object.fromEntries(
  Object.keys(unpacked)
    .sort((left, right) => left.localeCompare(right))
    .map((name) => [name, [unpacked[name], { mtime, os: 3, attrs: 0o644 << 16 }]]),
);
const archive = zipSync(entries, { level: 9, mtime, os: 3, attrs: 0o644 << 16 });
await writeFile(outputPath, archive, { mode: 0o644 });
await unlink(inputPath);
