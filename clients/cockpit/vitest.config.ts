// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — cockpit unit-test and coverage configuration

import { defineConfig } from "vitest/config";

// The cockpit's testable surface is its pure data logic AND its behavioural
// component layer. Logic tests run in the default node environment; component
// tests declare `// @vitest-environment jsdom` per file and render through
// @testing-library/react. The threshold gate stays on the logic modules, which
// are held to a full-coverage bar; components are asserted behaviourally (the
// canvas-drawing spine and the store-owning app shell keep paths jsdom cannot
// reach honestly, so a numeric gate there would invite performative tests).
export default defineConfig({
  test: {
    include: ["test/**/*.test.ts", "test/**/*.test.tsx"],
    setupFiles: ["test/setup.ts"],
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
