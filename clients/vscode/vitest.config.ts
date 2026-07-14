// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — VS Code client coverage policy

import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    testTimeout: 60_000,
    hookTimeout: 60_000,
    coverage: {
      provider: "v8",
      include: [
        "src/configurationReconnect.ts",
        "src/connectionState.ts",
        "src/evidenceModel.ts",
        "src/fleetModel.ts",
        "src/hubClosePolicy.ts",
        "src/hubEvidenceProtocol.ts",
        "src/hubJson.ts",
        "src/hubProtocol.ts",
        "src/hubTransport.ts",
        "src/hubTransportTimers.ts",
        "src/hubTransportTypes.ts",
        "src/reconnectPolicy.ts",
        "src/workspaceScope.ts",
      ],
      reporter: ["text", "json-summary"],
      thresholds: {
        branches: 95,
        functions: 95,
        lines: 95,
        statements: 95,
      },
    },
  },
});
