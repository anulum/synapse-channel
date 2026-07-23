// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — every dashboard request and cache path crosses the auth boundary

import { describe, expect, it } from "vitest";
import readmeSource from "../README.md?raw";
import workerSource from "../public/sw.js?raw";

// Guard the modules that construct HTTP reads/writes. Facades (auditFeeds,
// eventsTail, operatorActions, snapshot) re-export only and must stay free of
// a second fetch path; their transport/store owners carry authenticatedFetch.
const REQUEST_MODULES = [
  "access.ts",
  "auditFeedStore.ts",
  "causality.ts",
  "eventsTailSource.ts",
  "feed.ts",
  "history.ts",
  "liveTransport.ts",
  "merkleVerify.ts",
  "operatorActionTransport.ts",
  "snapshotStore.ts",
  "stateAt.ts",
] as const;

// Raw-source imports keep this meta-test inside the browser project's type
// surface: no Node filesystem API, the bundler hands over the text verbatim.
const LIB_SOURCES = import.meta.glob("../src/lib/*.ts", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

function source(relative: string): string {
  const text = LIB_SOURCES[relative];
  if (text === undefined) throw new Error(`guarded module missing from the raw-source glob: ${relative}`);
  return text;
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
    const authGuard = workerSource.indexOf('request.headers.has("Authorization")');
    const cacheRead = workerSource.indexOf("caches.match(request)");
    expect(authGuard).toBeGreaterThan(0);
    expect(cacheRead).toBeGreaterThan(authGuard);
    expect(workerSource).not.toContain("synapse-cockpit-bearer");
    expect(readmeSource).not.toContain("?token=");
  });
});
