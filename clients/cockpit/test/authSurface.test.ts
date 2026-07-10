// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — every dashboard request and cache path crosses the auth boundary

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const REQUEST_MODULES = [
  "causality.ts",
  "eventsTail.ts",
  "feed.ts",
  "history.ts",
  "merkleVerify.ts",
  "palette.ts",
  "snapshot.ts",
  "stateAt.ts",
] as const;

function source(relative: string): string {
  return readFileSync(new URL(relative, import.meta.url), "utf8");
}

describe("cockpit authenticated surface guard", () => {
  it("keeps every direct read and write constructor on the shared adapter", () => {
    for (const module of REQUEST_MODULES) {
      const text = source(`../src/lib/${module}`);
      expect(text, module).toContain('import { authenticatedFetch } from "./auth"');
      expect(text, module).not.toMatch(/(?:\?\?|=)\s*fetch[,;)\n]/u);
    }
  });

  it("never caches credential-bearing requests and publishes no query-token path", () => {
    const worker = source("../public/sw.js");
    const authGuard = worker.indexOf('request.headers.has("Authorization")');
    const cacheRead = worker.indexOf("caches.match(request)");
    expect(authGuard).toBeGreaterThan(0);
    expect(cacheRead).toBeGreaterThan(authGuard);
    expect(worker).not.toContain("synapse-cockpit-bearer");
    expect(source("../README.md")).not.toContain("?token=");
  });
});
