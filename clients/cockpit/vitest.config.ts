// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — cockpit unit-test and coverage configuration

import { defineConfig } from "vitest/config";

// The cockpit's testable surface is its pure data logic — snapshot parsing, the
// freshness contract, the polling store, and roster derivation. React view
// components are exercised by the build and by visual review (the host cannot
// render loopback pages for headless screenshots), so coverage is scoped to the
// logic modules, which are held to a full-coverage bar.
export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    coverage: {
      provider: "v8",
      include: ["src/lib/**"],
      reporter: ["text", "text-summary"],
      thresholds: {
        lines: 100,
        functions: 100,
        branches: 100,
        statements: 100,
      },
    },
  },
});
